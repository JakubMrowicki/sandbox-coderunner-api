import subprocess
import tempfile
import os
import json
import uuid
from flask import Flask, request, Response
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@app.route('/execute', methods=['POST'])
def execute():
    data = request.get_json()
    code = data.get('code')
    language = data.get('language')
    requirements = data.get('requirements')

    app.logger.info(f"Received request with language: {language}")

    if not code or not language:
        app.logger.error("Code or language not provided")
        return Response(json.dumps({'error': 'Code or language not provided'}), status=400, mimetype='application/json')

    def generate():
        try:
            container_id = None
            files_to_remove = []
            with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.py' if language == 'python' else '.sh') as tmp_file:
                tmp_file.write(code)
                tmp_file_path = tmp_file.name
            files_to_remove.append(tmp_file_path)
            app.logger.info(f"Created temporary file: {tmp_file_path}")

            yield json.dumps({'status': 'progress', 'message': 'Setting up gVisor sandbox...'}) + '\n'

            container_id = uuid.uuid4().hex
            app.logger.info(f"Generated container ID: {container_id}")

            with tempfile.TemporaryDirectory() as bundle_dir:
                app.logger.info(f"Created OCI bundle directory: {bundle_dir}")
                rootfs_dir = os.path.join(bundle_dir, 'rootfs')
                os.makedirs(rootfs_dir)

                # Define the OCI spec in a config.json file. This is the robust
                # way to configure the sandbox, avoiding CLI flag issues.
                config = {
                    "ociVersion": "1.0.2-dev",
                    "process": {
                        "terminal": False,
                        "user": {"uid": 0, "gid": 0},
                        "args": [],
                        "env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TERM=xterm"],
                        "cwd": "/"
                    },
                    "root": {"path": rootfs_dir, "readonly": True},
                    "hostname": container_id,
                    "mounts": [
                        {"destination": "/proc", "type": "proc", "source": "proc", "options": ["nosuid", "noexec", "nodev"]},
                        {"destination": "/dev", "type": "tmpfs", "source": "tmpfs", "options": ["nosuid", "strictatime", "mode=755", "size=65536k"]},
                        {"destination": "/dev/pts", "type": "devpts", "source": "devpts", "options": ["nosuid", "noexec", "newinstance", "ptmxmode=0666", "mode=0620", "gid=5"]},
                        {"destination": "/dev/shm", "type": "tmpfs", "source": "shm", "options": ["nosuid", "noexec", "nodev", "mode=1777", "size=65536k"]},
                        {"destination": "/dev/mqueue", "type": "mqueue", "source": "mqueue", "options": ["nosuid", "noexec", "nodev"]},
                        {"destination": "/sys", "type": "sysfs", "source": "sysfs", "options": ["nosuid", "noexec", "nodev", "ro"]},
                        # Add a writable /tmp for creating a venv and installing packages.
                        {"destination": "/tmp", "type": "tmpfs", "source": "tmpfs", "options": ["nosuid", "strictatime", "mode=1777", "size=1024m"]},
                    ], "linux": {
                        "gvisor": {},
                        "namespaces": [{"type": "pid"}, {"type": "ipc"}, {"type": "uts"}, {"type": "mount"}]
                    }
                }

                # To provide a basic filesystem for the sandbox, we bind-mount essential
                # host directories in a read-only fashion. This is necessary so that
                # the sandbox can find executables (like python3) and their libraries.
                host_dirs_to_mount = ['/bin', '/lib', '/usr']  # /etc is handled separately
                if os.path.exists('/lib64'):
                    host_dirs_to_mount.append('/lib64')

                for host_dir in host_dirs_to_mount:
                    config["mounts"].append({
                        "destination": host_dir,
                        "type": "bind",
                        "source": host_dir,
                        "options": ["ro", "rbind"]
                    })

                # Mount specific, essential files from /etc. Since we are using host networking,
                # we also mount the host's DNS and name resolution configuration.
                etc_essentials = [
                    "/etc/ssl",
                    "/etc/passwd",
                    "/etc/group",
                    "/etc/hosts",
                    "/etc/localtime",
                    "/etc/resolv.conf",
                    "/etc/nsswitch.conf",
                ]
                for item in etc_essentials:
                    if os.path.exists(item):
                        config["mounts"].append({
                            "destination": item,
                            "type": "bind",
                            "source": item,
                            "options": ["ro", "rbind"]
                        })

                sandbox_script_path = f"/sandbox/script.{'py' if language == 'python' else 'sh'}"
                config["mounts"].append({"destination": sandbox_script_path, "type": "bind", "source": tmp_file_path, "options": ["ro", "rbind"]})

                if language == 'python' and requirements:
                    yield json.dumps({'status': 'progress', 'message': 'Installing packages...'}) + '\n'
                    with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.txt', prefix='reqs-') as req_tmp_file:
                        req_tmp_file.write(requirements)
                        req_tmp_file_path = req_tmp_file.name
                    files_to_remove.append(req_tmp_file_path)
                    app.logger.info(f"Created temporary file for requirements: {req_tmp_file_path}")

                    sandbox_reqs_path = '/sandbox/requirements.txt'
                    config["mounts"].append({"destination": sandbox_reqs_path, "type": "bind", "source": req_tmp_file_path, "options": ["ro", "rbind"]})
                    # Create a venv, install packages into it, and then run the script with the venv's python.
                    # This is necessary to comply with PEP 668 (externally-managed environments).
                    venv_path = "/tmp/sandbox_venv"
                    install_cmd = (
                        f"python3 -m venv {venv_path} && "
                        f"{venv_path}/bin/pip install -q --no-cache-dir -r {sandbox_reqs_path} && "
                        f"{venv_path}/bin/python {sandbox_script_path}"
                    )
                    config["process"]["args"] = ['sh', '-c', install_cmd]
                elif language == 'python':
                    config["process"]["args"] = ['python3', sandbox_script_path]
                elif language == 'bash':
                    config["process"]["args"] = ['bash', sandbox_script_path]
                else:
                    app.logger.error(f"Unsupported language: {language}")
                    yield json.dumps({'error': 'Unsupported language'}) + '\n'
                    return

                config_path = os.path.join(bundle_dir, 'config.json')
                with open(config_path, 'w') as f:
                    json.dump(config, f)
                app.logger.info(f"Wrote OCI config to {config_path}")

                runsc_executable = '/usr/local/bin/runsc'
                # --rootless is good practice. The OCI bundle is created in a temporary
                # directory which is cleaned up after the process completes.
                command_to_run = [runsc_executable, '--rootless', '--network=host', 'run', '--bundle', bundle_dir, container_id]
                app.logger.info(f"Executing command: {' '.join(command_to_run)}")
                yield json.dumps({'status': 'progress', 'message': 'Executing code...'}) + '\n'
                process = subprocess.Popen(
                    command_to_run,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                stdout, stderr = process.communicate()
                exit_code = process.returncode

            app.logger.info(f"Execution finished with exit code: {exit_code}")
            app.logger.info(f"stdout: {stdout}")
            app.logger.info(f"stderr: {stderr}")

            yield json.dumps({'stdout': stdout, 'stderr': stderr, 'exit_code': exit_code}) + '\n'

        except Exception as e:
            app.logger.error(f"An error occurred: {e}")
            yield json.dumps({'error': str(e)}) + '\n'
        finally:
            for file_path in files_to_remove:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    app.logger.info(f"Removed temporary file: {file_path}")
            if container_id:
                runsc_executable = '/usr/local/bin/runsc'
                app.logger.info(f"Deleting container {container_id}...")
                delete_result = subprocess.run(
                    [runsc_executable, 'delete', container_id],
                    capture_output=True,
                    text=True
                )
                if delete_result.returncode != 0:
                    app.logger.warning(f"Could not delete container {container_id} (it may not have been created successfully): {delete_result.stderr.strip()}")
                else:
                    app.logger.info(f"Successfully deleted container {container_id}")

    return Response(generate(), mimetype='application/json')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
