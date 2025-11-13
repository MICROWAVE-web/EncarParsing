import datetime
import time

from f23qwre import load_cookies, make_api_request_with_cookies

def f():
    cookies = load_cookies()
    data = make_api_request_with_cookies(cookies)
    if cookies and data:
        print(data)
        print("✅ Работает с сохраненными куки!")
    else:
        print("❌ Нужны свежие куки через Selenium")


if __name__ == '__main__':
    while True:
        f()
        print(datetime.datetime.now())
        time.sleep(5)
