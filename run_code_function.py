"""
id: code_sandbox
title: Code Sandbox
description: Run arbitrary Python or Bash code safely in a gVisor sandbox. This tool requires a running Sandbox API.
author: jakubmrowicki
author_url: https://github.com/jakubmrowicki/sandbox_code
version: 0.0.3
license: Apache-2.0
"""
import asyncio
import json
import os
import pydantic
import sys
import typing
import inspect
import uuid
import requests


class _Action:
    LANGUAGE_PYTHON = "python"
    LANGUAGE_BASH = "bash"
    class Valves(pydantic.BaseModel):
        _VALVE_OVERRIDE_ENVIRONMENT_VARIABLE_NAME_PREFIX = "CODE_EVAL_VALVE_OVERRIDE_"
        SANDBOX_API_URL: str = pydantic.Field(
            default="http://sandboxrunner:5000/execute",
            description=f"URL of the Sandbox API; may be overridden by environment variable {_VALVE_OVERRIDE_ENVIRONMENT_VARIABLE_NAME_PREFIX}SANDBOX_API_URL.",
        )
        DEBUG: bool = pydantic.Field(
            default=False,
            description=f"Whether to produce debug logs during execution; may be overridden by environment variable {_VALVE_OVERRIDE_ENVIRONMENT_VARIABLE_NAME_PREFIX}DEBUG.",
        )

    def __init__(self, valves):
        self.valves = valves
        for valve_name, valve_value in valves.dict().items():
            override = os.getenv(
                self.valves._VALVE_OVERRIDE_ENVIRONMENT_VARIABLE_NAME_PREFIX
                + valve_name
            )
            if override is None:
                continue
            try:
                if type(valve_value) is type(True):
                    assert override.lower() in (
                        "true",
                        "false",
                    ), 'Value must be "true" or "false"'
                    override = override.lower() == "true"
                elif type(valve_value) is type(42):
                    override = int(override)
                elif type(valve_value) is type(""):
                    pass
                else:
                    valve_value_type = type(valve_value)
                    raise ValueError(f"Unknown valve type: {valve_value_type}")
            except Exception as e:
                raise ValueError(
                    f"Valve override {self.valves._VALVE_OVERRIDE_ENVIRONMENT_VARIABLE_NAME_PREFIX}{valve_name}={valve_value}: bad value: {e}"
                )
            else:
                setattr(self.valves, valve_name, override)

    async def action(
        self,
        body: dict,
        __event_emitter__: typing.Callable[[dict], typing.Any] = None,
        __id__: typing.Optional[str] = None,
        __user__: typing.Optional[dict] = None,
    ) -> typing.Optional[dict]:
        valves = self.valves
        debug = valves.DEBUG
        emitter = EventEmitter(__event_emitter__, debug=debug)
        execution_tracker: typing.Optional[CodeExecutionTracker] = None

        async def _fail(error_message, status="SANDBOX_ERROR"):
            if execution_tracker is not None:
                execution_tracker.set_error(error_message)
                await emitter.code_execution(execution_tracker)
            if debug:
                await emitter.fail(
                    f"[DEBUG MODE] {error_message}; body={body}; valves=[{valves}]"
                )
            else:
                await emitter.fail(error_message)
            return json.dumps({"status": status, "output": error_message})

        if len(body.get("messages", ())) == 0:
            return await _fail("No messages in conversation.", status="INVALID_INPUT")
        last_message = body["messages"][-1]
        if last_message["role"] != "assistant":
            return await _fail(
                "Last message was not from the AI model.", status="INVALID_INPUT"
            )
        split_three_backticks = last_message["content"].split("```")
        if len(split_three_backticks) < 3:
            return await _fail(
                "Last message did not contain code blocks.", status="INVALID_INPUT"
            )
        if len(split_three_backticks) % 2 != 1:
            return await _fail(
                "Last message did not contain well-formed code blocks.",
                status="INVALID_INPUT",
            )
        chosen_code_block = None
        language = None
        for code_block in split_three_backticks[-2:0:-2]:
            if code_block.startswith("python\n") or code_block.startswith("python3\n"):
                chosen_code_block = code_block
                language = self.LANGUAGE_PYTHON
            if (
                code_block.startswith("bash\n")
                or code_block.startswith("sh\n")
                or code_block.startswith("shell\n")
            ):
                chosen_code_block = code_block
                language = self.LANGUAGE_BASH
                break
        if chosen_code_block is None:
            # Try to see if the last code block looks like Python or bash.
            last_code_block = split_three_backticks[-2]
            # Look for an interpreter line.
            first_line = last_code_block.strip().split("\n")[0]
            if first_line.startswith("#!") and (
                first_line.endswith("python") or first_line.endswith("python3")
            ):
                chosen_code_block = code_block
                language = self.LANGUAGE_PYTHON
            elif first_line.startswith("#!") and first_line.endswith("sh"):
                chosen_code_block = code_block
                language = self.LANGUAGE_BASH
            elif any(
                python_like in last_code_block
                for python_like in ("import ", "print(", "print ")
            ):
                chosen_code_block = code_block
                language = self.LANGUAGE_PYTHON
            elif any(
                bash_like in last_code_block
                for bash_like in ("echo ", "if [", "; do", "esac\n")
            ):
                chosen_code_block = code_block
                language = self.LANGUAGE_BASH
        if chosen_code_block is None:
            return await _fail(
                "Message does not contain code blocks detected as Python or Bash."
            )

        try:
            status = "UNKNOWN"
            output = None
            generated_files = []
            language_title = language.title()

            # If the provided code starts/ends with "```SOME_LANGUAGE", remove that.
            code = chosen_code_block
            if language == self.LANGUAGE_PYTHON:
                code = code.removeprefix("python3")
                code = code.removeprefix("python")
            elif language == self.LANGUAGE_BASH:
                code = code.removeprefix("shell")
                code = code.removeprefix("bash")
                code = code.removeprefix("sh")
            code = code.strip()
            language_title = language.title()
            execution_tracker = CodeExecutionTracker(
                name=f"{language_title} code block", code=code, language=language
            )
            await emitter.clear_status()
            await emitter.code_execution(execution_tracker)

            result = await self._run_code(
                language=language,
                code=code,
                event_emitter=__event_emitter__,
            )
            status = result.get("status")
            output = result.get("output")
            generated_files = [] # _run_code doesn't return generated files, so initialize as empty

            if output:
                output = output.strip()
            execution_tracker.set_output(output)
            await emitter.code_execution(execution_tracker)
            if debug:
                await emitter.status(
                    status="complete" if status == "OK" else "error",
                    done=True,
                    description=f"[DEBUG MODE] status={status}; output={output}; valves=[{valves}]",
                )
            
            if status == "OK":
                await emitter.clear_status()
                generated_files_output = ""
                if len(generated_files) > 0:
                    generated_files_output = "* " + "\n* ".join(
                        f.markdown()
                        for f in sorted(generated_files, key=lambda f: f.name)
                    )
                if output and len(generated_files) > 0:
                    await emitter.message(
                        f"\n\n---\nI executed this {language_title} code and got:\n```Output\n{output}\n```\n**Files**:\n{generated_files_output}"
                    )
                elif output and len(generated_files) == 0:
                    await emitter.message(
                        f"\n\n---\nI executed this {language_title} code and got:\n```Output\n{output}\n```"
                    )
                elif len(generated_files) > 0:
                    await emitter.message(
                        f"\n\n---\nI executed this {language_title} code and it generated these files:\n{generated_files_output}"
                    )
                else:
                    await emitter.message(
                        f"\n\n---\nI executed this {language_title} code and it ran successfully with no output."
                    )
                return json.dumps(
                    {
                        "status": status,
                        "output": output,
                        "generated_files": {
                            f.name: f.markdown() for f in generated_files
                        },
                    }
                )
            if status == "TIMEOUT":
                if output:
                    await emitter.message(
                        f"\n\n---\nI executed this {language_title} code and it timed out after {self.valves.MAX_RUNTIME_SECONDS} seconds:\n```Error\n{output}\n```\n"
                    )
                else:
                    await emitter.message(
                        f"\n\n---\nI executed this {language_title} code and it timed out after {self.valves.MAX_RUNTIME_SECONDS} seconds.\n"
                    )
            elif status == "INTERRUPTED":
                if output:
                    await emitter.message(
                        f"\n\n---\nI executed this {language_title} code and used too many resources.\n```Error\n{output}\n```\n"
                    )
                else:
                    await emitter.message(
                        f"\n\n---\nI executed this {language_title} code and used too many resources.\n"
                    )
            elif status == "STORAGE_ERROR":
                await emitter.message(
                    f"\n\n---\nI executed this {language_title} code but it exceeded the storage quota.\n```Error\n{output}\n```\n"
                )
            elif status == "ERROR" and output:
                await emitter.message(
                    f"\n\n---\nI executed this {language_title} code and got the following error:\n```Error\n{output}\n```\n"
                )
            elif status == "ERROR":
                await emitter.message(
                    f"\n\n---\nI executed this {language_title} code but got an unexplained error.\n"
                )
            else:
                raise RuntimeError(
                    f"Unexplained status: {status} (output: {output})"
                )
            return json.dumps({"status": status, "output": output})
        except Exception as e:
            return await _fail(f"Unhandled exception: {e}")
    
    async def _run_code(
        self,
        language: str,
        code: str,
        event_emitter: typing.Callable[[dict], typing.Any] = None,
    ) -> str:
        """
        Run code safely by sending it to a sandbox API.

        :param language: Programming language of the code.
        :param code: The code to run.
        :param event_emitter: Event emitter to send status updates to.

        :return: A dictionary with the following fields: `status`, `output`.
        """
        valves = self.valves
        debug = valves.DEBUG
        emitter = EventEmitter(event_emitter, debug=debug)

        await emitter.status("Connecting to Sandbox API...")

        try:
            response = requests.post(
                valves.SANDBOX_API_URL,
                json={"code": code, "language": language},
                stream=True,
                timeout=30
            )
            response.raise_for_status()
            
            full_response = []
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    progress = json.loads(decoded_line)
                    if progress.get("status") == "progress":
                        await emitter.status(progress["message"])
                    else:
                        full_response.append(decoded_line)

            if not full_response:
                return {"status": "ERROR", "output": "No response from sandbox API"}

            final_output = json.loads(full_response[-1])

            await emitter.status("Code execution complete.")

            stdout = final_output.get("stdout")
            stderr = final_output.get("stderr")
            output = ""
            if stdout:
                output += stdout
            if stderr:
                if output:
                    output += "\n"
                output += stderr
            
            return {
                "status": "OK" if final_output.get("exit_code") == 0 else "ERROR",
                "output": output,
            }

        except requests.exceptions.RequestException as e:
            await emitter.fail(f"Sandbox API connection error: {e}")
            return {"status": "ERROR", "output": f"Sandbox API connection error: {e}"}
        except json.JSONDecodeError as e:
            await emitter.fail(f"Sandbox API response error: {e}")
            return {"status": "ERROR", "output": f"Sandbox API response error: {e}"}


class Action:
    Valves = _Action.Valves

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __event_emitter__: typing.Callable[[dict], typing.Any] = None,
    ) -> typing.Optional[dict]:
        return await _Action(self.valves).action(
            body=body, __event_emitter__=__event_emitter__
        )


class EventEmitter:
    """
    Helper wrapper for OpenWebUI event emissions.
    """

    def __init__(
        self,
        event_emitter: typing.Callable[[dict], typing.Any] = None,
        debug: bool = False,
    ):
        self.event_emitter = event_emitter
        self._debug = debug
        self._status_prefix = None
        self._emitted_status = False

    def set_status_prefix(self, status_prefix):
        self._status_prefix = status_prefix

    async def _emit(self, typ, data, twice):
        if self._debug:
            print(f"Emitting {typ} event: {data}", file=sys.stderr)
        if not self.event_emitter:
            return None
        result = None
        for i in range(2 if twice else 1):
            maybe_future = self.event_emitter(
                {
                    "type": typ,
                    "data": data,
                }
            )
            if asyncio.isfuture(maybe_future) or inspect.isawaitable(maybe_future):
                result = await maybe_future
        return result

    async def status(
        self, description="Unknown state", status="in_progress", done=False
    ):
        self._emitted_status = True
        if self._status_prefix is not None:
            description = f"{self._status_prefix}{description}"
        await self._emit(
            "status",
            {
                "status": status,
                "description": description,
                "done": done,
            },
            twice=not done and len(description) <= 1024,
        )

    async def fail(self, description="Unknown error"):
        await self.status(description=description, status="error", done=True)

    async def clear_status(self):
        if not self._emitted_status:
            return
        self._emitted_status = False
        await self._emit(
            "status",
            {
                "status": "complete",
                "description": "",
                "done": True,
            },
            twice=True,
        )

    async def message(self, content):
        await self._emit(
            "message",
            {
                "content": content,
            },
            twice=False,
        )

    async def citation(self, document, metadata, source):
        await self._emit(
            "citation",
            {
                "document": document,
                "metadata": metadata,
                "source": source,
            },
            twice=False,
        )

    async def code_execution(self, code_execution_tracker):
        await self._emit(
            "citation", code_execution_tracker._citation_data(), twice=True
        )


class CodeExecutionTracker:
    def __init__(self, name, code, language):
        self._uuid = str(uuid.uuid4())
        self.name = name
        self.code = code
        self.language = language
        self._result = {}

    def set_error(self, error):
        self._result["error"] = error

    def set_output(self, output):
        self._result["output"] = output

    def add_file(self, name, url):
        if "files" not in self._result:
            self._result["files"] = []
        self._result["files"].append(
            {
                "name": name,
                "url": url,
            }
        )

    def _citation_data(self):
        data = {
            "type": "code_execution",
            "id": self._uuid,
            "name": self.name,
            "code": self.code,
            "language": self.language,
        }
        if "output" in self._result or "error" in self._result:
            data["result"] = self._result
        return data