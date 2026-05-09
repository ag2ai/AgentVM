import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests

from agentvm.providers.base import Provider

logger = logging.getLogger("desktopenv.providers.apptainer.ApptainerProvider")
logger.setLevel(logging.INFO)

WAIT_TIME = 3
RETRY_INTERVAL = 2
LOCK_TIMEOUT = 10


class ApptainerProvider(Provider):
    """
    Provider that runs the OSWorld VM inside an Apptainer/Singularity instance.

    This mirrors the Docker-based flow but uses `apptainer instance start/stop`
    instead of Docker. Networking is host-based (no port mapping), so the VM
    HTTP server and related services are exposed directly on the host:
      - API    -> localhost:5000
      - VNC    -> localhost:8006
      - Chrome -> localhost:9222
      - VLC    -> localhost:8080

    Note: only a single Apptainer-based VM should be running on a host at a time,
    otherwise ports will clash.
    """

    def __init__(self, region: str):
        super().__init__(region)

        self.instance_name: Optional[str] = None
        self.server_port = 5000
        self.chromium_port = 9222
        self.vnc_port = 8006
        self.vlc_port = 8080

        # Resource knobs – aligned with Docker provider defaults
        self.environment = {
            "DISK_SIZE": os.environ.get("OSWORLD_DISK_SIZE", "32G"),
            "RAM_SIZE": os.environ.get("OSWORLD_RAM_SIZE", "4G"),
            "CPU_CORES": os.environ.get("OSWORLD_CPU_CORES", "4"),
        }

        project_root = Path(__file__).resolve().parent.parent.parent.parent
        default_image_path = project_root / "osworld.sif"
        self.image_path = Path(os.environ.get("OSWORLD_APPTAINER_IMAGE", str(default_image_path)))
        # Image reference used when building the SIF from a Docker image
        self.image_ref = os.environ.get("OSWORLD_APPTAINER_IMAGE_REF", "docker://happysixd/osworld-docker:latest")

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------
    @staticmethod
    def _check_apptainer_available():
        """Ensure that `apptainer` is installed and on PATH."""
        try:
            result = subprocess.run(
                ["apptainer", "--version"],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.debug(f"Apptainer version: {result.stdout.strip()}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(
                "Apptainer/Singularity is not available on this system. "
                "Please install Apptainer and ensure it is on PATH."
            ) from e

    def _build_image_if_needed(self):
        """
        Build the SIF image from the configured Docker image if it does not exist.

        This runs:
            apptainer build /path/to/osworld.sif docker://happysixd/osworld-docker:latest

        On some systems this may require elevated privileges; in that case we
        raise a clear error so users can build the image manually.
        """
        if self.image_path.exists():
            logger.info(f"Using existing Apptainer image: {self.image_path}")
            return

        logger.info(f"Apptainer image not found, building: {self.image_path}")
        self.image_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            subprocess.run(
                [
                    "apptainer",
                    "build",
                    str(self.image_path),
                    self.image_ref,
                ],
                check=True,
            )
            logger.info(f"Successfully built Apptainer image: {self.image_path}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to build Apptainer image: {e}")
            raise RuntimeError(
                f"Failed to build Apptainer image at {self.image_path}. "
                "You may need to build it manually, for example:\n"
                f"  apptainer build {self.image_path} {self.image_ref}"
            ) from e

    def _wait_for_vm_ready(self, timeout: int = 300):
        """Wait for the VM HTTP API to be ready by polling /screenshot."""
        start_time = time.time()

        def check_screenshot() -> bool:
            try:
                resp = requests.get(
                    f"http://localhost:{self.server_port}/screenshot",
                    timeout=(10, 10),
                )
                return resp.status_code == 200
            except Exception:
                return False

        while time.time() - start_time < timeout:
            if check_screenshot():
                return True
            logger.info("Checking if Apptainer VM is ready...")
            time.sleep(RETRY_INTERVAL)

        raise TimeoutError("Apptainer VM failed to become ready within timeout period")

    # -------------------------------------------------------------------------
    # Provider interface
    # -------------------------------------------------------------------------
    def start_emulator(self, path_to_vm: str, headless: bool, os_type: str):
        """
        Start the Apptainer instance that hosts the OSWorld VM.

        Args:
            path_to_vm: Path to QCOW2 image (e.g., Ubuntu.qcow2).
            headless: Currently unused, kept for API parity.
            os_type: OS type, unused here but kept for API parity.
        """
        del headless, os_type  # Unused, but kept for interface compatibility

        self._check_apptainer_available()
        self._build_image_if_needed()

        qcow2_path = os.path.abspath(path_to_vm)
        if not os.path.isfile(qcow2_path):
            raise FileNotFoundError(f"QCOW2 image not found at: {qcow2_path}")

        # Create a reasonably unique name per process
        self.instance_name = f"osworldvm-{int(time.time())}-{os.getpid()}"

        # Handle optional KVM acceleration
        binds = [f"{qcow2_path}:/System.qcow2:ro"]
        if os.path.exists("/dev/kvm"):
            binds.append("/dev/kvm")
            logger.info("KVM device found, using hardware acceleration")
        else:
            self.environment["KVM"] = "N"
            logger.warning(
                "KVM device not found, running without hardware acceleration (will be slower)"
            )

        # Explicitly set USER_PORTS to ensure QEMU forwards these ports in user-mode networking
        # This is required because Apptainer usually falls back to user-mode networking
        # where the container's bridge interface (and thus NAT) is not available.
        user_ports = [
            str(self.server_port),
            str(self.chromium_port),
            str(self.vlc_port),
        ]
        self.environment["USER_PORTS"] = ",".join(user_ports)

        # Build command
        cmd = [
            "apptainer",
            "instance",
            "start",
            "--writable-tmpfs",
        ]

        for bind in binds:
            cmd.extend(["--bind", bind])

        # Pass resource environment variables into the container
        # Use multiple --env flags to avoid issues with commas in values
        for k, v in self.environment.items():
            cmd.extend(["--env", f"{k}={v}"])

        cmd.append(str(self.image_path))
        cmd.append(self.instance_name)

        logger.info(f"Starting Apptainer instance '{self.instance_name}'...")
        logger.debug("Apptainer command: %s", " ".join(cmd))

        try:
            subprocess.run(cmd, check=True)
            logger.info(f"Apptainer instance '{self.instance_name}' started.")

            # Wait for the VM HTTP API to be ready
            self._wait_for_vm_ready()
        except Exception as e:
            logger.error(f"Error starting Apptainer instance: {e}")
            # Best-effort cleanup
            try:
                if self.instance_name:
                    subprocess.run(
                        ["apptainer", "instance", "stop", self.instance_name],
                        check=False,
                    )
            except Exception:
                pass
            self.instance_name = None
            raise

    def get_ip_address(self, path_to_vm: str) -> str:
        """
        Return the connection string for DesktopEnv.

        For Apptainer we use host networking and fixed ports, so we encode:
            'localhost:<server>:<chrome>:<vnc>:<vlc>'
        similar to the Docker provider to allow DesktopEnv to override defaults.
        """
        del path_to_vm  # Unused

        if not self.instance_name:
            raise RuntimeError("VM not started - Apptainer instance is not running")

        return f"localhost:{self.server_port}:{self.chromium_port}:{self.vnc_port}:{self.vlc_port}"

    def save_state(self, path_to_vm: str, snapshot_name: str):
        """
        Snapshotting is not supported for Apptainer-based VMs.
        """
        del path_to_vm, snapshot_name
        raise NotImplementedError("Snapshots are not available for Apptainer provider")

    def revert_to_snapshot(self, path_to_vm: str, snapshot_name: str):
        """
        For stateless Apptainer-based VMs we simply stop the current instance.
        A new instance will be started by the environment when needed.
        """
        del snapshot_name
        self.stop_emulator(path_to_vm)

    def stop_emulator(self, path_to_vm: str, region=None, *args, **kwargs):
        # Note: region parameter is ignored for Apptainer provider
        # but kept for interface consistency with other providers
        del path_to_vm, region, args, kwargs

        if not self.instance_name:
            return

        logger.info(f"Stopping Apptainer VM instance '{self.instance_name}'...")
        try:
            subprocess.run(
                ["apptainer", "instance", "stop", self.instance_name],
                check=False,
            )
            time.sleep(WAIT_TIME)
        except Exception as e:
            logger.error(f"Error stopping Apptainer instance '{self.instance_name}': {e}")
        finally:
            self.instance_name = None


