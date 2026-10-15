from unittest.mock import Mock

import launch


def test_desktop_launcher_binds_to_localhost(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    start = Mock(return_value=process)
    monkeypatch.setattr(launch, 'ready', Mock(side_effect=[False, True]))
    monkeypatch.setattr(launch.subprocess, 'Popen', start)
    browser = Mock()
    monkeypatch.setattr(launch.webbrowser, 'open', browser)
    launch.main()
    command = start.call_args.args[0]
    assert command[command.index('--server.address') + 1] == 'localhost'
    assert command[command.index('--browser.serverAddress') + 1] == 'localhost'
    browser.assert_called_once_with(launch.URL)
    process.wait.assert_called_once_with()


def test_existing_dashboard_opens_without_starting_another_server(monkeypatch):
    start, browser = Mock(), Mock()
    monkeypatch.setattr(launch, 'ready', lambda: True)
    monkeypatch.setattr(launch.subprocess, 'Popen', start)
    monkeypatch.setattr(launch.webbrowser, 'open', browser)
    launch.main()
    start.assert_not_called()
    browser.assert_called_once_with(launch.URL)
