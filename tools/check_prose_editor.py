"""用独立浏览器会话验证中文撤销与输入时的控件稳定性（不操作已有用户窗口）。

先启动工作台，再运行：
uv run --with playwright python tools/check_prose_editor.py --url http://127.0.0.1:8786
需要已安装的 Chrome。真实系统拼音候选窗仍需人工验收。
"""
import argparse
import sys
from playwright.sync_api import expect, sync_playwright


def check_chinese_undo(page, editor, modifier):
    cdp = page.context.new_cdp_session(page)
    try:
        for pinyin, text in [('nihao', '你好'), ('shijie', '世界'), ('jixu', '继续')]:
            cdp.send('Input.imeSetComposition', {
                'text': pinyin, 'selectionStart': len(pinyin), 'selectionEnd': len(pinyin),
            })
            cdp.send('Input.insertText', {'text': text})
        expect(editor).to_have_text('你好世界继续')
        for text in ['你好世界', '你好', '']:
            page.keyboard.press(f'{modifier}+z')
            expect(editor).to_have_text(text)
    finally:
        cdp.detach()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8786')
    args = parser.parse_args()
    modifier = 'Meta' if sys.platform == 'darwin' else 'Control'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(args.url)
            page.get_by_role('button', name='长文', exact=True).click()
            editor = page.locator('.wb-fb .cm-content')
            expect(editor).to_be_visible()
            page.wait_for_timeout(300)  # 页面初始聚焦/锚点完成；各次中文提交之间不留分组停顿。
            editor.click()
            check_chinese_undo(page, editor, modifier)
            for text in ['你好', '你好世界', '你好世界继续']:
                page.keyboard.press(f'{modifier}+Shift+z')
                expect(editor).to_have_text(text)
            page.wait_for_timeout(300)
            selectors = ('.wb-list', '.wb-prog', '.wb-history')
            original_ids = {selector: page.locator(selector).get_attribute('id') for selector in selectors}
            assert all(original_ids.values())
            page.keyboard.type(' one two three', delay=20)
            page.keyboard.press(f'{modifier}+z')
            expect(editor).to_have_text('你好世界继续')
            page.wait_for_timeout(700)  # 包含输入回调与延迟自动保存的完整一轮。
            for selector, element_id in original_ids.items():
                assert page.locator(selector).get_attribute('id') == element_id, f'{selector} was rebuilt while editing'
            page.keyboard.type('(')
            expect(editor).to_have_text('你好世界继续(')
            expect(editor).to_have_attribute('aria-label', '修改意见')
            editor.press('Tab')
            expect(editor).not_to_be_focused()
            print('PASS: rapid Chinese undo/redo, stable surrounding controls, prose punctuation and Tab navigation')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
