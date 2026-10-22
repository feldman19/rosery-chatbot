"""Listener ownership must precede background activity (no provider calls)."""
import socket
import tempfile
import unittest
from unittest.mock import patch

from server import make_server


class ExclusiveListenerTests(unittest.TestCase):
    def test_second_bind_fails_without_starting_background_work(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = make_server(0, first_dir, workspace='manager')
            try:
                self.assertFalse(first.allow_reuse_address)
                self.assertFalse(first.allow_reuse_port)
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    self.assertEqual(first.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
                with patch('server.LiveReports.start') as live, patch('server.PreparationLoop.start_worker') as preparation, patch('server.CampaignOperations.start_worker') as execution:
                    with self.assertRaises(OSError):
                        make_server(first.server_port, second_dir, workspace='manager', start_live=True)
                    live.assert_not_called()
                    preparation.assert_not_called()
                    execution.assert_not_called()
            finally:
                first.server_close()

    def test_worker_start_failure_stops_started_services_and_releases_port(self):
        with tempfile.TemporaryDirectory() as directory:
            # Choose a currently free port; the following make_server owns it.
            probe = socket.socket()
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
            probe.close()
            with patch('server.LiveReports.start') as live, patch('server.LiveReports.stop') as stopped, patch('server.PreparationLoop.start_worker', side_effect=RuntimeError('startup failed')), patch('server.PreparationLoop.stop') as preparation_stop, patch('server.CampaignOperations.start_worker') as execution, patch('server.CampaignOperations.stop') as execution_stop:
                with self.assertRaisesRegex(RuntimeError, 'startup failed'):
                    make_server(port, directory, workspace='manager', start_live=True)
                live.assert_called_once()
                stopped.assert_called_once()
                preparation_stop.assert_called_once()
                execution_stop.assert_called_once()
                execution.assert_not_called()
            replacement = make_server(port, directory, workspace='manager')
            replacement.server_close()


if __name__ == '__main__':
    unittest.main()
