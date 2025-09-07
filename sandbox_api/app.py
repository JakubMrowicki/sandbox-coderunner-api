import subprocess
import tempfile
import os
import json
from flask import Flask, request, Response

app = Flask(__name__)

@app.route('/execute', methods=['POST'])
def execute():
    data = request.get_json()
    code = data.get('code')
    language = data.get('language')

    if not code or not language:
        return Response(json.dumps({'error': 'Code or language not provided'}), status=400, mimetype='application/json')

    def generate():
        try:
            with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.py' if language == 'python' else '.sh') as tmp_file:
                tmp_file.write(code)
                tmp_file_path = tmp_file.name

            yield json.dumps({'status': 'progress', 'message': 'Setting up gVisor sandbox...'}) + '\n'

            runsc_cmd = [
                'runsc',
                '--network=none',
                'run',
                '--'
            ]
            
            if language == 'python':
                interpreter_cmd = ['python3', tmp_file_path]
            elif language == 'bash':
                interpreter_cmd = ['bash', tmp_file_path]
            else:
                yield json.dumps({'error': 'Unsupported language'}) + '\n'
                return

            process = subprocess.Popen(
                runsc_cmd + interpreter_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            yield json.dumps({'status': 'progress', 'message': 'Executing code...'}) + '\n'

            stdout, stderr = process.communicate()
            exit_code = process.returncode

            yield json.dumps({'stdout': stdout, 'stderr': stderr, 'exit_code': exit_code}) + '\n'

        except Exception as e:
            yield json.dumps({'error': str(e)}) + '\n'
        finally:
            if 'tmp_file_path' in locals() and os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)

    return Response(generate(), mimetype='application/json')

if __name__ == '__main__':
    app.run(debug=True, port=5000)