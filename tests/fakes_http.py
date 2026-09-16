"""Подмена aiohttp.ClientSession для тестов AI-провайдеров без сети
(паттерн _verify_ai_providers.py)."""


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _RespCtx:
    """Асинхронный контекст `async with session.post(...) as resp`.

    При входе забирает следующий ответ из очереди; исключение в очереди
    пробрасывается как сетевая ошибка.
    """

    def __init__(self, queue):
        self._queue = queue

    async def __aenter__(self):
        if not self._queue:
            raise AssertionError("Нет заготовленного HTTP-ответа для этого запроса")
        resp = self._queue.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def __aexit__(self, *args):
        return False


def make_session_class(*resps):
    """Фабрика фейковой сессии: каждый post()/get()/request() забирает следующий
    ответ из очереди. Возвращает (класс, список зарегистрированных запросов)."""
    queue = list(resps)
    calls: list = []

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, data=None, json=None, headers=None):
            calls.append({"method": "POST", "url": url, "data": data,
                          "json": json, "headers": headers})
            return _RespCtx(queue)

        def get(self, url, headers=None):
            calls.append({"method": "GET", "url": url, "headers": headers})
            return _RespCtx(queue)

        def request(self, method, url, headers=None, json=None, timeout=None):
            calls.append({"method": method, "url": url, "json": json,
                          "headers": headers, "timeout": timeout})
            return _RespCtx(queue)

    return FakeSession, calls