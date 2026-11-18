import httpx

# r = httpx.get("https://httpbin.org/get", params = {"q": "books", "page": 2} )

# print(r.request)

# print(a for a in dir(r.request) if not a.startswith("_"))

# print([a for a in dir(r.request) if not a.startswith("_")])

# r2 = httpx.post("https://httpbin.org/post", data = {"user": "fabr"})


# print([b for b in dir(r2) if not b.startswith("_")])


# print(r2.json()["form"])



with httpx.Client(follow_redirects=True, timeout = 10) as client:
    print([c for c in dir(client) if not c.startswith("_")])

    login_page = client.get(f"https://quotes.toscrape.com/login")
    