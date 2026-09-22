import pytest

from app.security import UnsafeUrlError, is_public_ip, validate_public_url


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "192.168.1.2", "169.254.169.254", "::1", "fc00::1"])
def test_private_ips_blocked(ip):
    assert not is_public_ip(ip)


@pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip):
    assert is_public_ip(ip)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a", "http://localhost/a", "http://127.0.0.1/a", "http://169.254.169.254/latest/meta-data/"])
async def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeUrlError):
        await validate_public_url(url)


@pytest.mark.asyncio
async def test_dns_rebinding_style_private_resolution_rejected():
    async def resolver(host):
        return {"93.184.216.34", "10.0.0.5"}
    with pytest.raises(UnsafeUrlError):
        await validate_public_url("https://example.test/file", resolver=resolver)


@pytest.mark.asyncio
async def test_public_resolution_allowed():
    async def resolver(host):
        return {"93.184.216.34"}
    assert await validate_public_url("https://example.test/file", resolver=resolver)
