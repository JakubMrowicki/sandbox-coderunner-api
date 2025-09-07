import subprocess
import tempfile
import os
import json
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

    app.logger.info(f"Received request with language: {language}")

    if not code or not language:
        app.logger.error("Code or language not provided")
        return Response(json.dumps({'error': 'Code or language not provided'}), status=400, mimetype='application/json')

    def generate():
        try:
            with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.py' if language == 'python' else '.sh') as tmp_file:
                tmp_file.write(code)
                tmp_file_path = tmp_file.name
            app.logger.info(f"Created temporary file: {tmp_file_path}")

            yield json.dumps({'status': 'progress', 'message': 'Setting up gVisor sandbox...'}) + '\n'

            runsc_cmd = [
                'runsc',
                '--network=none',
                'do'
            ]
            
            if language == 'python':
                interpreter_cmd = ['python3', tmp_file_path]
            elif language == 'bash':
                interpreter_cmd = ['bash', tmp_file_path]
            else:
                app.logger.error(f"Unsupported language: {language}")
                yield json.dumps({'error': 'Unsupported language'}) + '\n'
                return

            command_to_run = runsc_cmd + interpreter_cmd
            app.logger.info(f"Executing command: {' '.join(command_to_run)}")
            process = subprocess.Popen(
                command_to_run,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            yield json.dumps({'status': 'progress', 'message': 'Executing code...'}) + '\n'

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
            if 'tmp_file_path' in locals() and os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)
                app.logger.info(f"Removed temporary file: {tmp_file_path}")

    return Response(generate(), mimetype='application/json')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
