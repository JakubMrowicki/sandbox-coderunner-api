# Plan for Refactoring `run_code.py` to use a Sandbox API

This document outlines the plan to decouple code execution from `run_code.py` by introducing a dedicated Sandbox API. This will enhance security by isolating the execution environment within a gVisor sandbox in a separate LXC container.

## 1. `run_code.py` (Client) Modifications

The existing `run_code.py` script will be updated to act as a client. Instead of executing code locally, it will delegate the execution to the new Sandbox API.

### Key Changes:

1.  **Remove Local Code Execution:** The current logic that uses `subprocess` or a similar module to run code directly will be removed.
2.  **Add HTTP Client Logic:**
    - The `requests` library will be used to make HTTP POST requests. This will be added as a dependency.
    - A new function will be created to handle the API communication.
3.  **API Request Formulation:**
    - The code to be executed will be packaged into a JSON payload. The format will be `{"code": "<code_string>"}`.
    - This payload will be sent to the Sandbox API's `/execute` endpoint.
4.  **Configuration:**
    - The URL of the Sandbox API will not be hardcoded. It will be configurable via an environment variable (e.g., `SANDBOX_API_URL`) with a sensible default (e.g., `http://localhost:5000/execute`).
5.  **Response Handling:**
    - The script will parse the JSON response from the API, which will contain the `stdout`, `stderr`, and `exit_code`.
    - Error handling will be implemented to manage network issues (e.g., connection errors) or non-200 responses from the API.
    - Progress reporting as updates are received from the API (e.g. "Connecting to Sandbox API" > "Successfully connected" > "Setting up gVisor sandbox..." etc etc)

## 2. Sandbox API Server (New Component)

A new, lightweight web server will be created. This server's sole responsibility is to receive code, execute it within a gVisor sandbox, and return the results.

### Technology Stack:

- **Language:** Python
- **Framework:** Flask for its simplicity.
- **Containerization:** Proxmox LXC

### Implementation Details:

1.  **Create a New Project:** A new directory (e.g., `sandbox_api`) will be created for the server code.
2.  **API Endpoint:**
    - A single `POST /execute` endpoint will be created.
    - It will expect a JSON body with a `code` field: `{"code": "print('hello')"}`.
3.  **Execution Logic:**
    - Upon receiving a request, the server will extract the code from the JSON payload.
    - The code will be written to a temporary file.
    - The server will use `subprocess.run()` to execute the code using `runsc`. The command will be structured like:
      ```bash
      runsc --network=none python /path/to/temp_file.py
      ```
    - `--network=none` will be used to prevent network access from the sandboxed code, enhancing security.
    - The `stdout`, `stderr`, and `returncode` from the execution will be captured.
4.  **API Response:**
    - **Success:** If the code executes, the server will return a JSON response with the captured output:
      ```json
      {
        "stdout": "...",
        "stderr": "...",
        "exit_code": 0
      }
      ```
    - **Error:** If the server fails to execute the code, it will return an appropriate HTTP error status and a JSON body with an error message.
5.  **Dependencies:** A `requirements.txt` file will be created for the server, containing `Flask`.

## 3. Step-by-Step Implementation Strategy

1.  **Step 1: Initial Sandbox Server Setup**

    - Create the `sandbox_api` directory.
    - Create a `requirements.txt` with `Flask`.
    - Create an `app.py` with a basic Flask server and a placeholder `/execute` endpoint that returns a mock success response.

2.  **Step 2: Refactor `run_code.py`**

    - Modify `run_code.py` to remove the old execution logic.
    - Add the `requests` dependency.
    - Implement the new logic to call the (mocked) `/execute` endpoint and print the response.
    - Ensure it's configurable via the `SANDBOX_API_URL` environment variable.

3.  **Step 3: Implement Sandbox Execution**

    - In `sandbox_api/app.py`, implement the full execution logic using `subprocess.run` and `runsc`.
    - Handle temporary file creation and cleanup.
    - Capture and return the actual `stdout`, `stderr`, and `exit_code`.

4.  **Step 4: Testing and Integration**

    - Run the Sandbox API server.
    - Run the modified `run_code.py` and verify that it correctly sends a request and receives the result from the server.
    - Test with various code snippets, including ones that produce errors, to ensure robust handling.

5.  **Step 5: Documentation**
    - Create a `README.md` for the `sandbox_api` project explaining how to build, configure, and run it.
    - Update any relevant documentation for `run_code.py` to reflect the new architecture and the `SANDBOX_API_URL` environment variable.
