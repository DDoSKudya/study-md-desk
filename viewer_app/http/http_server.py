"""
This module defines an HTTP server variant used by the application to
serve HTTP requests in a threaded, restart-friendly way.

It exposes a handler and a threaded server implementation for other
parts of the system to use.

It imports HTTPServer and mixes it with Python ThreadingMixIn to add
per-request threading behavior.

It also imports a Handler class from viewer_app.http.http_handler, which
is intended to process individual HTTP requests.

The ReuseAddrServer class subclasses HTTPServer and sets
allow_reuse_address = True so the OS socket can be rebound quickly
after shutdown.

This class relies entirely on the base HTTPServer implementation for
request handling and lifecycle, only altering the address reuse
setting.

The ThreadedServer class combines ThreadingMixIn and ReuseAddrServer to
create a threaded HTTP server that also supports address reuse.

By inheriting from these two classes, it gains concurrent request
handling via threads and smoother restart behavior while keeping the
default HTTP server semantics.

The __all__ definition restricts what is exported when the module is
imported with a wildcard to Handler and ThreadedServer.

Elsewhere in the application, this module serves as the entry point for
constructing the HTTP server that backs the viewers web interface or
API.
"""

from __future__ import annotations

from http.server import HTTPServer
from socketserver import ThreadingMixIn

from viewer_app.http.http_handler import Handler

__all__ = ["Handler", "ThreadedServer"]


class ReuseAddrServer(HTTPServer):
    """
    An HTTP server variant that allows its socket address to be reused
    immediately after shutdown.

    It is intended to make rapid restart cycles smoother during
    development or controlled restarts.

    This class only changes the allow_reuse_address flag from the base
    HTTPServer and otherwise inherits all behavior unchanged.
    """

    allow_reuse_address = True


class ThreadedServer(ThreadingMixIn, ReuseAddrServer):
    """
    A threaded HTTP server that supports address reuse for concurrent
    clients.

    It combines multi-connection handling with a restart-friendly socket
    configuration.

    Methods:
        All public methods are inherited from ThreadingMixIn and
        ReuseAddrServer, including request handling, lifecycle
        management, and thread dispatching for each incoming connection.
    """
