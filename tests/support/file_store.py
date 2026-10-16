"""测试对象存储的稳定 URL 协议；文件字节仍由各集成夹具保存。"""

from urllib.parse import quote, unquote


class FileURLStore:
    def public_url(self, key):
        return "https://files.example.test/" + quote(key, safe="/")

    def key_from_url(self, url):
        if not url.startswith("https://files.example.test/"):
            raise ValueError("文件地址不正确")
        key = unquote(url.removeprefix("https://files.example.test/"))
        if self.public_url(key) != url:
            raise ValueError("文件地址不正确")
        return key

    async def put_public(self, key, data, content_type):
        await self.put(key, data, content_type)
        return self.public_url(key)
