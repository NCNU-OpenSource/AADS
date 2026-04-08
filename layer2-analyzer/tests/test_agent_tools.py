"""
Tests for Agent Tools

Tests the whitelisting, safety mechanisms, and basic functionality
of Agent diagnostic tools.
"""
import pytest
from agent.tools import execute_diagnostic_command


@pytest.mark.asyncio
async def test_execute_diagnostic_command_whitelist_allowed():
    """Test that whitelisted commands are allowed"""
    # These commands should be allowed
    allowed_commands = [
        'ps aux',
        'netstat -an',
        'df -h',
        'docker ps',
        'uptime',
        'free -m'
    ]

    for cmd in allowed_commands:
        result = await execute_diagnostic_command.ainvoke({"command": cmd})
        assert 'BLOCKED' not in result, f"Command '{cmd}' should be allowed"


@pytest.mark.asyncio
async def test_execute_diagnostic_command_whitelist_blocked():
    """Test that non-whitelisted commands are blocked"""
    blocked_commands = [
        'rm -rf /',
        'dd if=/dev/zero of=/dev/sda',
        'mkfs.ext4 /dev/sda1',
        'random_command_that_does_not_exist'
    ]

    for cmd in blocked_commands:
        result = await execute_diagnostic_command.ainvoke({"command": cmd})
        assert 'BLOCKED' in result, f"Command '{cmd}' should be blocked"


@pytest.mark.asyncio
async def test_execute_diagnostic_command_forbidden_patterns():
    """Test that forbidden patterns are detected"""
    dangerous_commands = [
        'ps aux > /tmp/output',  # Output redirection
        'echo "test" >> /etc/hosts',  # Append redirection
        'sudo ps aux',  # Sudo
        'chmod 777 /tmp',  # Chmod
        'chown root:root /tmp'  # Chown
    ]

    for cmd in dangerous_commands:
        result = await execute_diagnostic_command.ainvoke({"command": cmd})
        assert 'BLOCKED' in result, f"Dangerous command '{cmd}' should be blocked"


@pytest.mark.asyncio
async def test_execute_diagnostic_command_curl_localhost_only():
    """Test that curl/wget are restricted to localhost"""
    # Localhost should be allowed
    localhost_commands = [
        'curl http://localhost:8080/health',
        'curl http://127.0.0.1:9090/metrics',
        'wget http://0.0.0.0:3000/api'
    ]

    for cmd in localhost_commands:
        result = await execute_diagnostic_command.ainvoke({"command": cmd})
        # Should not be blocked for remote hosts
        assert 'BLOCKED: curl/wget only allowed for localhost' not in result

    # Remote hosts should be blocked
    remote_commands = [
        'curl http://google.com',
        'wget http://example.com/file.tar.gz'
    ]

    for cmd in remote_commands:
        result = await execute_diagnostic_command.ainvoke({"command": cmd})
        assert 'BLOCKED' in result, f"Remote curl/wget '{cmd}' should be blocked"


@pytest.mark.asyncio
async def test_execute_diagnostic_command_real_execution():
    """Test actual command execution with safe commands"""
    # Test 'uptime' command (should always work)
    result = await execute_diagnostic_command.ainvoke({"command": "uptime"})
    assert 'BLOCKED' not in result
    assert 'load average' in result.lower() or 'up' in result.lower()

    # Test 'echo' (simple test command)
    result = await execute_diagnostic_command.ainvoke({"command": "ps --version"})
    assert 'BLOCKED' not in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
