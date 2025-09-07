# Sandbox API

This is a simple Flask-based API that executes code in a gVisor sandbox.

## Setup

This API is designed to run in a dedicated, isolated environment. A Proxmox LXC container is the recommended setup, but a regular VM can also be used.

### Proxmox LXC Container Setup (Recommended)

1.  **Create the LXC Container:**
    *   In the Proxmox web interface, click "Create CT".
    *   **General:** Give the container a hostname and password.
    *   **Template:** Use a recent version of a minimal Linux distribution (e.g., Debian or Ubuntu).
    *   **Disks:** A disk size of 8 GB should be sufficient.
    *   **CPU:** 1 or 2 cores should be enough.
    *   **Memory:** 512 MB of RAM is a good starting point.
    *   **Network:** Use a bridged or NATed network, depending on your network configuration.
    *   **DNS:** Use your network's DNS servers.
    *   **Confirm and Finish.**

2.  **Container Configuration:**
    *   Once the container is created, select it in the Proxmox interface and go to **Options**.
    *   **Enable Nesting and Keyctl:**
        *   Select "Features" and click "Edit".
        *   Enable both "nesting" and "keyctl". This is required for gVisor to work correctly.

3.  **Install Dependencies inside the LXC Container:**
    *   Start the container and open a console session.
    *   Update the package manager:
        ```bash
        apt update && apt upgrade -y
        ```
    *   Install Python, pip, and venv:
        ```bash
        apt install python3 python3-pip python3-venv -y
        ```
    *   **Install gVisor (`runsc`):** Follow the official gVisor installation instructions for your chosen distribution. You can find them here: [https://gvisor.dev/docs/user_guide/install/](https://gvisor.dev/docs/user_guide/install/)

4.  **Deploy the Sandbox API:**
    *   Clone this repository into the LXC container.
    *   Install the Python dependencies:
        ```bash
        python3 -m venv venv
        source venv/bin/activate
        pip install -r requirements.txt
        ```

### Can a regular VM be used?

Yes, a regular VM (e.g., from Proxmox, VirtualBox, or VMWare) can be used as well. The setup process is similar:

1.  Create a VM with a minimal Linux distribution.
2.  Install Python, pip, and venv.
3.  Install gVisor (`runsc`).
4.  Deploy the Sandbox API as described above.

The main advantage of using an LXC container is that it is more lightweight than a full VM, which means it will use fewer resources on your Proxmox host.

## Running the API

```bash
python app.py
```

The API will be available at `http://<container_or_vm_ip>:5000`.

## API Endpoint

### POST /execute

Executes the given code in a sandbox.

**Request Body:**

```json
{
  "code": "print('hello')",
  "language": "python"
}
```

**Response:**

A stream of JSON objects. The last object will contain the final result.

**Success Response:**

```json
{
  "stdout": "hello\n",
  "stderr": "",
  "exit_code": 0
}
```

**Error Response:**

```json
{
  "error": "..."
}
```