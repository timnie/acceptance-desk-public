// Generated from Python by Transcrypt 3.9.5. DO NOT EDIT.
var cm = {};
import {AssertionError, AttributeError, BaseException, DeprecationWarning, Exception, IndexError, IterableError, KeyError, NotImplementedError, RuntimeWarning, StopIteration, UserWarning, ValueError, Warning, __JsIterator__, __PyIterator__, __Terminal__, __add__, __and__, __call__, __class__, __envir__, __eq__, __floordiv__, __ge__, __get__, __getcm__, __getitem__, __getslice__, __getsm__, __gt__, __i__, __iadd__, __iand__, __idiv__, __ijsmod__, __ilshift__, __imatmul__, __imod__, __imul__, __in__, __init__, __ior__, __ipow__, __irshift__, __isub__, __ixor__, __jsUsePyNext__, __jsmod__, __k__, __kwargtrans__, __le__, __lshift__, __lt__, __matmul__, __mergefields__, __mergekwargtrans__, __mod__, __mul__, __ne__, __neg__, __nest__, __or__, __pow__, __pragma__, __pyUseJsNext__, __rshift__, __setitem__, __setproperty__, __setslice__, __sort__, __specialattrib__, __sub__, __super__, __t__, __terminal__, __truediv__, __withblock__, __xor__, _sort, abs, all, any, assert, bin, bool, bytearray, bytes, callable, chr, delattr, dict, dir, divmod, filter, float, getattr, hasattr, hex, input, int, isinstance, issubclass, len, list, map, max, min, object, oct, ord, pow, print, property, py_TypeError, py_enumerate, py_iter, py_metatype, py_next, py_reversed, py_typeof, range, repr, round, set, setattr, sorted, str, sum, tuple, zip} from './org.transcrypt.__runtime__.js';
import {history_boundary} from './history_policy.js';
import * as __module_cm__ from 'nicegui-codemirror';
__nest__ (cm, '', __module_cm__);
var __name__ = '__main__';
export var install_editor = function (root) {
	if (root.dataset.proseHistory == 'ready') {
		return ;
	}
	var view = cm.EditorView.findFromDOM (root);
	if (!(view)) {
		return ;
	}
	var previous = dict ({'event': ''});
	var boundary = function (transaction) {
		if (!(transaction.docChanged)) {
			return null;
		}
		var event = transaction.annotation (cm.Transaction.userEvent) || '';
		var isolation = history_boundary (event, previous ['event'], !(transaction.startState.selection.main.empty));
		previous ['event'] = event;
		if (isolation) {
			return dict ({'annotations': cm.isolateHistory.of (isolation)});
		}
		return null;
	};
	var prose_language = function () {
		var args = tuple ([].slice.apply (arguments).slice (0));
		return [dict ({'closeBrackets': dict ({'brackets': []})})];
	};
	var extensions = [cm.EditorState.transactionExtender.of (boundary), cm.EditorState.languageData.of (prose_language)];
	view.dispatch (dict ({'effects': cm.StateEffect.appendConfig.of (extensions)}));
	view.setTabFocusMode (true);
	view.contentDOM.setAttribute ('aria-label', root.closest ('.wb-prose').getAttribute ('aria-label'));
	root.dataset.proseHistory = 'ready';
};
export var scan = function () {
	var args = tuple ([].slice.apply (arguments).slice (0));
	for (var root of document.querySelectorAll ('.wb-prose .cm-editor')) {
		install_editor (root);
	}
};
export var observer = new window.MutationObserver (scan);
observer.observe (document.body, dict ({'childList': true, 'subtree': true}));
scan ();
