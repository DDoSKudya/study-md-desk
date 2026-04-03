"""
This module provides a small helper for starting and tracking a local
server instance running on the loopback interface.

It is likely used by other parts of the system to spin up an HTTP-like
server for a UI, API, or local integration without exposing it publicly.

The module defines a RunningServer dataclass that holds the server
object, the chosen port, and the background thread running the server
loop.

RunningServer also exposes a base_url property that formats the bound
port into a usable http://127.0.0.1:<port>/ URL string.

The core function start_local_server tries to bind a given server class
with a handler class to the first available port in a specified range.

If a suitable port is found, it starts the servers serve_forever loop
in a daemon thread with some common errors suppressed and returns a
RunningServer instance.

If no ports in the range are available, start_local_server raises an
OSError to signal failure to the caller.
"""

from contextlib import suppress
from threading import Thread

from dataclasses import dataclass


@dataclass
class RunningServer:
    """
    Represent a running local server and its execution context.

    This dataclass bundles the server instance, its listening port, and
    the thread that is serving requests.

    Attributes:
        server (type):
            The concrete server object that is actively listening for
            and handling incoming connections.
        port (int):
            The TCP port on the loopback interface where the server is
            bound and accepting requests.
        thread (Thread):
            The daemon thread running the server's main loop so it can
            serve requests in the background.
    """

    server: type
    port: int
    thread: Thread

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


def start_local_server(
    server_cls: type,
    handler_cls: type,
    start_port: int = 8765,
    end_port: int = 8775,
) -> RunningServer:
    """
    Start a local HTTP-like server on an available loopback port.

    This function searches a port range for a free port, starts the
    server in a background thread, and returns information about the
    running instance.

    Args:
        server_cls (type):
            The server class to instantiate, expected to accept an
            address tuple and a handler class.
        handler_cls (type):
            The request handler class used by the server to process
            incoming connections.
        start_port (int):
            The first port in the inclusive range of candidate ports to
            try when binding the server.
        end_port (int):
            The end of the half-open port range to scan for
            availability; this port itself is not tried.

    Returns:
        RunningServer:
            A data object containing the created server instance, the
            bound port number, and the background thread running the
            server loop.

    Raises:
        OSError:
            If no free port can be found in the requested port range or
            the server fails to bind for all attempted ports.
    """
    for port in range(start_port, end_port):
        try:
            server = server_cls(("127.0.0.1", port), handler_cls)
            break
        except OSError:
            continue
    else:
        raise OSError(
            f"Failed to start local server: ports {start_port}-{end_port - 1} are busy"
        )

    def serve() -> None:
        """
        Serve requests in a background thread.

        This function runs the server's serve_forever loop in a
        background thread, handling common exceptions and ensuring the
        thread can exit cleanly.

        It uses contextlib.suppress to ignore OSError (port binding
        failures) and RuntimeError (server shutdown) to prevent thread
        crashes from affecting the main process.
        """
        with suppress(OSError, RuntimeError):
            server.serve_forever()

    thread: Thread = Thread(target=serve, daemon=True)
    thread.start()
    return RunningServer(server=server, port=port, thread=thread)
