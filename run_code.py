"""
id: sandbox_code
title: Sandbox Code
description: Run arbitrary Python or Bash code safely in a gVisor sandbox. This tool requires a running Sandbox API. The URL of the API can be configured using the SANDBOX_API_URL environment variable.
author: jakubmrowicki
author_url: https://github.com/jakubmrowicki/sandbox_code
version: 0.0.2
license: Apache-2.0
"""

import json
import os
import pydantic
import requests
import typing
import argparse
import sys
import asyncio
import inspect

class _Tools:
    class Valves(pydantic.BaseModel):
        _VALVE_OVERRIDE_ENVIRONMENT_VARIABLE_NAME_PREFIX = "CODE_EVAL_VALVE_OVERRIDE_"
        SANDBOX_API_URL: str = pydantic.Field(
            default="http://localhost:5000/execute",
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
                else:
                    valve_value_type = type(valve_value)
                    raise ValueError(f"Unknown valve type: {valve_value_type}")
            except Exception as e:
                raise ValueError(
                    f"Valve override {self.valves._VALVE_OVERRIDE_ENVIRONMENT_VARIABLE_NAME_PREFIX}{valve_name}={valve_value}: bad value: {e}"
                )
            else:
                setattr(self.valves, valve_name, override)

    async def run_bash_command(
        self,
        bash_command: str,
        __event_emitter__: typing.Callable[[dict], typing.Any] = None,
    ) -> str:
        """
        Run a bash command-line or script safely in a gVisor sandbox.

        :param bash_command: Bash command or script to run.

        :return: A JSON object with the following fields: `bash_command`, `status`, `output`. In most cases, when `status` is "OK", the user is interested in the content of the `output` field. Otherwise, report the `status` field first.
        """
        result = await self._run_code(
            language="bash",
            code=bash_command,
            event_emitter=__event_emitter__,
        )
        return json.dumps(
            {
                "bash_command": bash_command,
                "status": result["status"],
                "output": result["output"],
            },
            ensure_ascii=False,
        )

    async def run_python_code(
        self,
        python_code: str,
        __event_emitter__: typing.Callable[[dict], typing.Any] = None,
    ) -> str:
        """
        Run Python code safely in a gVisor sandbox.

        :param python_code: Python code to run.

        :return: A JSON object with the following fields: `python_code`, `status`, `output`. In most cases, when `status` is "OK", the user is interested in the content of the `output` field. Otherwise, report the `status` field first.
        """
        result = await self._run_code(
            language="python",
            code=python_code,
            event_emitter=__event_emitter__,
        )
        return json.dumps(
            {
                "python_code": python_code,
                "status": result["status"],
                "output": result["output"],
            },
            ensure_ascii=False,
        )

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
            
            return {
                "status": "OK" if final_output.get("exit_code") == 0 else "ERROR",
                "output": final_output.get("stdout") or final_output.get("stderr"),
            }

        except requests.exceptions.RequestException as e:
            await emitter.fail(f"Sandbox API connection error: {e}")
            return {"status": "ERROR", "output": f"Sandbox API connection error: {e}"}
        except json.JSONDecodeError as e:
            await emitter.fail(f"Sandbox API response error: {e}")
            return {"status": "ERROR", "output": f"Sandbox API response error: {e}"}


class Tools:
    Valves = _Tools.Valves

    def __init__(self):
        self.valves = self.Valves()

    async def run_bash_command(
        self,
        bash_command: str,
        __event_emitter__: typing.Callable[[dict], typing.Any] = None,
    ) -> str:
        return await _Tools(self.valves).run_bash_command(
            bash_command=bash_command,
            __event_emitter__=__event_emitter__,
        )

    async def run_python_code(
        self,
        python_code: str,
        __event_emitter__: typing.Callable[[dict], typing.Any] = None,
    ) -> str:
        return await _Tools(self.valves).run_python_code(
            python_code=python_code,
            __event_emitter__=__event_emitter__,
        )

class EventEmitter:
    def __init__(
        self,
        event_emitter: typing.Callable[[dict], typing.Any] = None,
        debug: bool = False,
    ):
        self.event_emitter = event_emitter
        self._debug = debug

    async def _emit(self, typ, data):
        if self._debug:
            print(f"Emitting {typ} event: {data}", file=sys.stderr)
        if not self.event_emitter:
            return None
        maybe_future = self.event_emitter(
            {
                "type": typ,
                "data": data,
            }
        )
        if asyncio.isfuture(maybe_future) or inspect.isawaitable(maybe_future):
            return await maybe_future
        return None

    async def status(self, description="Unknown state", status="in_progress", done=False):
        await self._emit(
            "status",
            {
                "status": status,
                "description": description,
                "done": done,
            },
        )

    async def fail(self, description="Unknown error"):
        await self.status(description=description, status="error", done=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self_test", action="store_true")
    args = parser.parse_args()

    async def main():
        if args.self_test:
            code = 'print("Hello world!")'
        else:
            code = sys.stdin.read()
        
        tools = Tools()
        result = await tools.run_python_code(python_code=code)
        print(result)

    asyncio.run(main())
