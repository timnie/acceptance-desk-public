"""浏览器适配的 Python 源码；用 Transcrypt 编译，生成物不可手改。"""
from org.transcrypt.stubs.browser import document, window
import cm
from history_policy import history_boundary


def install_editor(root):
    if root.dataset.proseHistory == 'ready':
        return
    view = cm.EditorView.findFromDOM(root)
    if not view:
        return
    previous = {'event': ''}

    def boundary(transaction):
        if not transaction.docChanged:
            return None
        event = transaction.annotation(cm.Transaction.userEvent) or ''
        isolation = history_boundary(event, previous['event'], not transaction.startState.selection.main.empty)
        previous['event'] = event
        if isolation:
            return {'annotations': cm.isolateHistory.of(isolation)}
        return None

    def prose_language(*args):
        return [{'closeBrackets': {'brackets': []}}]

    extensions = [cm.EditorState.transactionExtender.of(boundary),
                  cm.EditorState.languageData.of(prose_language)]
    view.dispatch({'effects': cm.StateEffect.appendConfig.of(extensions)})
    view.setTabFocusMode(True)
    view.contentDOM.setAttribute('aria-label', root.closest('.wb-prose').getAttribute('aria-label'))
    root.dataset.proseHistory = 'ready'


def scan(*args):
    for root in document.querySelectorAll('.wb-prose .cm-editor'):
        install_editor(root)


observer = __new__(window.MutationObserver(scan))
observer.observe(document.body, {'childList': True, 'subtree': True})
scan()
