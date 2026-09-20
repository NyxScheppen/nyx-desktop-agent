"""Desktop pairing is private to the two launch chains, never VITE_* config."""
# pyright: reportPrivateUsage=false

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import dev
import nyx.main as entry


def test_desktop_launcher_pairs_backend_and_tauri(tmp_path: Path) -> None:
    process = MagicMock()
    process.poll.return_value = 0
    process.returncode = 0
    with (
        patch.object(dev.sys, "argv", ["dev.py", "--desktop"]),
        patch.object(dev.shutil, "which", return_value="npm"),
        patch.object(dev, "_BACKEND_PID_FILE", tmp_path / "backend.pid"),
        patch.object(dev, "_LAUNCH_LOCK_FILE", tmp_path / "launcher.lock"),
        patch.object(dev, "_find_launcher_pids", return_value=[]),
        patch.object(dev, "_find_listening_backend_pids", return_value=[]),
        patch.object(dev.socket, "socket") as listener,
        patch.object(dev.subprocess, "Popen", return_value=process) as spawn,
    ):
        dev.main()
    listener.return_value.__enter__.return_value.bind.assert_called_once_with(
        ("127.0.0.1", 8000)
    )
    backend, desktop = spawn.call_args_list
    assert desktop.args[0] == ["npm", "run", "tauri", "dev"]
    secret = backend.kwargs["env"]["NYX_BROWSER_BOOTSTRAP_SECRET"]
    assert len(bytes.fromhex(secret)) == 32
    assert desktop.kwargs["env"]["NYX_BROWSER_BOOTSTRAP_SECRET"] == secret


def test_occupied_backend_port_refuses_before_secret_distribution(
    tmp_path: Path,
) -> None:
    with (
        patch.object(dev.sys, "argv", ["dev.py", "--desktop"]),
        patch.object(dev.shutil, "which", return_value="npm"),
        patch.object(dev, "_BACKEND_PID_FILE", tmp_path / "backend.pid"),
        patch.object(dev, "_LAUNCH_LOCK_FILE", tmp_path / "launcher.lock"),
        patch.object(dev, "_find_launcher_pids", return_value=[]),
        patch.object(dev, "_find_listening_backend_pids", return_value=[]),
        patch.object(dev.socket, "socket") as listener,
        patch.object(dev.subprocess, "Popen") as spawn,
    ):
        listener.return_value.__enter__.return_value.bind.side_effect = OSError()
        with pytest.raises(SystemExit, match="1"):
            dev.main()
    spawn.assert_not_called()


def test_occupied_backend_port_refuses_plain_launcher(tmp_path: Path) -> None:
    with (
        patch.object(dev.sys, "argv", ["dev.py"]),
        patch.object(dev.shutil, "which", return_value="npm"),
        patch.object(dev, "_BACKEND_PID_FILE", tmp_path / "backend.pid"),
        patch.object(dev, "_LAUNCH_LOCK_FILE", tmp_path / "launcher.lock"),
        patch.object(dev, "_find_launcher_pids", return_value=[]),
        patch.object(dev, "_find_listening_backend_pids", return_value=[]),
        patch.object(dev.socket, "socket") as listener,
        patch.object(dev.subprocess, "Popen") as spawn,
    ):
        listener.return_value.__enter__.return_value.bind.side_effect = OSError()
        with pytest.raises(SystemExit, match="1"):
            dev.main()
    spawn.assert_not_called()


def test_launcher_lock_replaces_live_owner_before_restart(tmp_path: Path) -> None:
    lock_file = tmp_path / "launcher.lock"
    lock_file.write_text("1234", encoding="ascii")
    with (
        patch.object(dev, "_LAUNCH_LOCK_FILE", lock_file),
        patch.object(
            dev, "_launcher_process_matches", side_effect=[True, False, False]
        ),
        patch.object(dev.os, "getpid", return_value=5678),
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.subprocess, "run") as run,
    ):
        fd = dev._acquire_launcher_lock()
        try:
            assert lock_file.read_text(encoding="ascii") == "5678"
        finally:
            dev._release_launcher_lock(fd)
    run.assert_called_once_with(
        ["taskkill", "/PID", "1234", "/T", "/F"],
        capture_output=True,
        check=False,
    )


def test_launcher_lock_waits_for_old_lock_handle_to_close(tmp_path: Path) -> None:
    lock_file = tmp_path / "launcher.lock"
    lock_file.write_text("1234", encoding="ascii")
    real_unlink = Path.unlink
    unlink_calls = 0

    def unlink(path: Path, *, missing_ok: bool = False) -> None:
        nonlocal unlink_calls
        if path == lock_file and unlink_calls == 0:
            unlink_calls += 1
            raise PermissionError("lock still closing")
        unlink_calls += 1
        real_unlink(path, missing_ok=missing_ok)

    with (
        patch.object(dev, "_LAUNCH_LOCK_FILE", lock_file),
        patch.object(
            dev, "_launcher_process_matches", side_effect=[True, False, False]
        ),
        patch.object(dev.os, "getpid", return_value=5678),
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.subprocess, "run"),
        patch.object(Path, "unlink", unlink),
        patch.object(dev.time, "sleep"),
    ):
        fd = dev._acquire_launcher_lock()
        try:
            assert lock_file.read_text(encoding="ascii") == "5678"
        finally:
            dev._release_launcher_lock(fd)
    assert unlink_calls == 3


def test_launcher_lock_replaces_stale_owner(tmp_path: Path) -> None:
    lock_file = tmp_path / "launcher.lock"
    lock_file.write_text("1234", encoding="ascii")
    with (
        patch.object(dev, "_LAUNCH_LOCK_FILE", lock_file),
        patch.object(dev, "_launcher_process_matches", return_value=False),
        patch.object(dev.os, "getpid", return_value=5678),
        patch.object(dev.os, "name", "nt"),
    ):
        fd = dev._acquire_launcher_lock()
        try:
            assert lock_file.read_text(encoding="ascii") == "5678"
        finally:
            dev._release_launcher_lock(fd)
    assert not lock_file.exists()


def test_launcher_stops_legacy_launchers(tmp_path: Path) -> None:
    with (
        patch.object(dev, "_find_launcher_pids", return_value=[1234, 5678]),
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.subprocess, "run") as run,
    ):
        dev._stop_legacy_launchers()
    assert run.call_args_list == [
        ((["taskkill", "/PID", "1234", "/T", "/F"],),
         {"capture_output": True, "check": False}),
        ((["taskkill", "/PID", "5678", "/T", "/F"],),
         {"capture_output": True, "check": False}),
    ]


def test_launcher_stops_recorded_backend_before_restart(tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text("1234", encoding="ascii")
    with (
        patch.object(dev, "_BACKEND_PID_FILE", pid_file),
        patch.object(dev, "_find_listening_backend_pids", return_value=[]),
        patch.object(dev, "_backend_process_matches", return_value=True),
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.subprocess, "run") as run,
    ):
        dev._stop_previous_backend()
    run.assert_called_once_with(
        ["taskkill", "/PID", "1234", "/T", "/F"],
        capture_output=True,
        check=False,
    )
    assert not pid_file.exists()


def test_launcher_stops_legacy_backend_without_pid_file(tmp_path: Path) -> None:
    pid_file = tmp_path / "backend.pid"
    with (
        patch.object(dev, "_BACKEND_PID_FILE", pid_file),
        patch.object(dev, "_find_listening_backend_pids", return_value=[1234, 5678]),
        patch.object(dev, "_backend_process_matches", return_value=True),
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.subprocess, "run") as run,
    ):
        dev._stop_previous_backend()
    assert run.call_args_list == [
        ((["taskkill", "/PID", "1234", "/T", "/F"],),
         {"capture_output": True, "check": False}),
        ((["taskkill", "/PID", "5678", "/T", "/F"],),
         {"capture_output": True, "check": False}),
    ]


def test_launcher_scan_excludes_current_process_ancestors() -> None:
    result = MagicMock(stdout="29840,7084\n30072,29840\n1234,9999\n")
    with (
        patch.object(dev.os, "name", "nt"),
        patch.object(dev.os, "getpid", return_value=30072),
        patch.object(dev.subprocess, "run", return_value=result),
    ):
        assert dev._find_launcher_pids() == [1234]


async def test_packaged_entry_resolves_resources_without_launch_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    (tmp_path / "config.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv("NYX_CONFIG", raising=False)
    monkeypatch.delenv("NYX_CANON_DIR", raising=False)
    build = AsyncMock(side_effect=RuntimeError("stop before runtime"))
    monkeypatch.setattr(entry, "build_app_context", build)
    with pytest.raises(RuntimeError, match="stop before runtime"):
        await entry.main()
    config = build.call_args.args[0]
    assert config.embedding.model == str(tmp_path / "embedding-model")
    assert entry.os.environ["NYX_CANON_DIR"] == str(tmp_path / "prompts")


def test_sidecar_build_bundles_only_public_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import huggingface_hub

    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev.sys, "argv", ["dev.py", "--build-sidecar"])
    monkeypatch.setattr(
        huggingface_hub, "snapshot_download",
        MagicMock(return_value=str(tmp_path / "model")),
    )
    process = MagicMock(stdout="x86_64-pc-windows-msvc\n")
    with (
        patch.object(dev.subprocess, "run", return_value=process) as run,
        patch.object(
            dev.subprocess, "Popen",
            side_effect=AssertionError("no services during build"),
        ),
        patch.object(dev.shutil, "copy2") as copy,
    ):
        dev.main()
    command = run.call_args_list[-1].args[0]
    assert command[:3] == [dev.sys.executable, "-m", "PyInstaller"]
    assert f"{tmp_path / 'prompts'};prompts" in command
    assert not any(".env" in argument for argument in command)
    assert "x86_64-pc-windows-msvc" in str(copy.call_args.args[1])


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("NYX_RUN_DESKTOP_SPIKES") != "1",
    reason="Opt-in Windows desktop / local HTTPS mock IdP spike",
)
def test_local_https_mock_idp_create_popup(tmp_path: Path) -> None:
    import datetime
    import ipaddress
    import ssl
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Nyx fixture CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]), critical=False).sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    (tmp_path / "cert.pem").write_bytes(pem)
    (tmp_path / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    paths: list[str] = []

    class MockIdp(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            paths.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self) -> None:
            paths.append(self.path)
            if self.path in ("/redirect-hop", "/unsafe-redirect"):
                self.send_response(302)
                target = "/callback" if self.path == "/redirect-hop" else (
                    f"https://localhost:{server.server_port}/unsafe-target"
                )
                self.send_header("Location", target)
                self.end_headers()
                return
            pages = {
                "/opener": """<title>Remote fixture</title><script>
                  window.addEventListener('message', e => {
                    if(e.origin===location.origin) window.result=e.data;
                  });
                </script><main>Mock IdP opener</main>""",
                "/authorize": """<script>
                  document.cookie='nyxFixture=one; Secure; SameSite=Lax; path=/';
                  window.open('/nested');
                  location.href='/redirect-hop';
                </script>""",
                "/callback": """<title>Untrusted title</title><script>
                  (async()=>{
                    let denied=false;
                    try { await window.__TAURI_INTERNALS__.invoke('acl_probe'); }
                    catch(e) { denied=String(e).includes('not allowed'); }
                    opener.postMessage({ok:opener.location.pathname==='/opener',
                      denied}, location.origin); window.close();
                  })();
                </script>""",
                "/refused": (
                    "<title>Untrusted title</title>"
                    "Mock provider refuses embedded login"
                ),
            }
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(pages.get(self.path, "refused").encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockIdp)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    bridge = ThreadingHTTPServer(("127.0.0.1", 0), MockIdp)
    bridge_worker = threading.Thread(target=bridge.serve_forever, daemon=True)
    bridge_worker.start()
    fixture_env = os.environ.copy()
    fixture_env["NYX_OAUTH_FIXTURE_ORIGIN"] = f"https://127.0.0.1:{server.server_port}"
    fixture_env["NYX_OAUTH_FIXTURE_CERT"] = pem.decode()
    fixture_env["NYX_OAUTH_FIXTURE_BRIDGE_PORT"] = str(bridge.server_port)
    try:
        result = subprocess.run(
            ["cargo", "test", "--lib", "local_https_idp_create_popup", "--",
             "--ignored", "--nocapture"],
            cwd=dev.ROOT / "frontend" / "src-tauri", env=fixture_env,
            capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr + repr(paths)
        assert paths.count("/authorize") == 1 and "/callback" in paths
        assert "/nested" not in paths
        assert "/unsafe-target" not in paths
        assert not any(path.endswith("/pages") for path in paths)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        bridge.shutdown()
        bridge.server_close()
        bridge_worker.join(timeout=2)
