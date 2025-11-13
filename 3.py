from f23qwre import load_cookies, make_api_request_with_cookies

cookies = load_cookies()
if cookies and make_api_request_with_cookies(cookies):
    print("✅ Работает с сохраненными куки!")
else:
    print("❌ Нужны свежие куки через Selenium")