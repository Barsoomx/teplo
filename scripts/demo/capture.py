import time
from pathlib import Path

from playwright.sync_api import Browser, Error, Page, sync_playwright

SERVERS = {'en': 'http://teplo-demo-en:8088', 'ru': 'http://teplo-demo-ru:8088'}
LOCALES = {'en': 'en-GB', 'ru': 'ru-RU'}
DESKTOP = {'viewport': {'width': 1280, 'height': 800}, 'device_scale_factor': 1.5}
PHONE = {'viewport': {'width': 390, 'height': 844}, 'device_scale_factor': 2, 'is_mobile': True, 'has_touch': True}
HERO_SIZE = {'width': 1440, 'height': 800}
HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent.parent / 'docs' / 'assets'
RAW = Path('/tmp/teplo-shots')  # noqa: S108
SERVER_WAIT_SECONDS = 90


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for language in SERVERS:
            _wait_for_server(browser, language)
        _shoot(browser, 'en', 'dark', DESKTOP, {'dashboard': RAW / 'hero-desktop.png'})
        _shoot(browser, 'en', 'light', PHONE, {'journal': RAW / 'hero-phone.png'})
        _shoot(browser, 'en', 'light', DESKTOP, {'dashboard': ASSETS / 'dashboard-light.png'})
        _shoot(browser, 'ru', 'dark', PHONE, {'dashboard': ASSETS / 'phone-dashboard-ru.png'})
        _shoot(browser, 'ru', 'light', PHONE, {'journal': ASSETS / 'phone-journal-ru.png'})
        _shoot(browser, 'ru', 'dark', PHONE, {'login': ASSETS / 'phone-login-ru.png'})
        _render_hero(browser)
        browser.close()


def _wait_for_server(browser: Browser, language: str) -> None:
    page = browser.new_page()
    deadline = time.monotonic() + SERVER_WAIT_SECONDS
    while True:
        try:
            page.goto(f'{SERVERS[language]}/login/')
        except Error:
            if time.monotonic() > deadline:
                raise

            time.sleep(1)
        else:
            page.close()
            return


def _shoot(browser: Browser, language: str, scheme: str, device: dict, targets: dict[str, Path]) -> None:
    base = SERVERS[language]
    context = browser.new_context(
        color_scheme=scheme,
        locale=LOCALES[language],
        **device,
    )
    page = context.new_page()
    _open(page, f'{base}/login/')
    if 'login' in targets:
        page.screenshot(path=targets['login'])
    page.fill('#id_username', 'owner')
    page.fill('#id_password', 'demo')
    page.click('button[type=submit]')
    page.wait_for_url(f'{base}/')
    _open(page, f'{base}/')
    if 'dashboard' in targets:
        page.screenshot(path=targets['dashboard'])
    if 'journal' in targets:
        _open(page, f'{base}/journal/')
        page.screenshot(path=targets['journal'])
    context.close()


def _open(page: Page, url: str) -> None:
    page.goto(url)
    page.wait_for_load_state('networkidle')
    page.evaluate('document.fonts.ready')


def _render_hero(browser: Browser) -> None:
    page = browser.new_page(viewport=HERO_SIZE, device_scale_factor=1.5)
    _open(page, (HERE / 'hero.html').as_uri())
    page.screenshot(path=ASSETS / 'hero.png')
    page.close()


if __name__ == '__main__':
    main()
