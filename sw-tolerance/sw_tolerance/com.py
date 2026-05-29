"""Binding-agnostic COM member invocation.

pywin32 exposes COM members two different ways depending on how the object was
dispatched:

* **Early binding** (``gencache.EnsureDispatch``) — backed by a generated
  type-library wrapper. A no-arg member such as ``GetSheetNames`` is a bound
  method you must *call*: ``model.GetSheetNames()``.

* **Late binding** (``win32com.client.Dispatch``) — the fallback used when
  makepy can't introspect the type library (the common SolidWorks first-run
  case, see ``sw_client.connect``). pywin32 has no type info here, so attribute
  access on a *no-arg* member is resolved as a property get: ``model.GetSheetNames``
  already returns the tuple, and calling it (``...()``) raises
  ``'tuple' object is not callable``.

``call`` papers over that difference so the rest of the package can be written
once and run correctly under either binding mode.

This module is intentionally free of COM imports so it stays importable (and
unit-testable) on macOS.
"""
from __future__ import annotations


def call(obj, name, *args):
    """Invoke COM member ``name`` on ``obj``, robust to early/late binding.

    Members that take arguments are always real callables under both binding
    modes, so they are called directly. No-arg members are the ambiguous case:
    under late binding the attribute access has *already* invoked the member
    and returned its value (a scalar, ``None``, a tuple, or another dispatch
    object), so it must not be called a second time.
    """
    member = getattr(obj, name)
    if args:
        return member(*args)
    if callable(member) and not _is_dispatch(member):
        return member()
    return member


def _is_dispatch(member) -> bool:
    """True if ``member`` is a live COM object rather than a bound method.

    Late-binding dynamic dispatch objects are themselves callable — they expose
    the COM default member via ``__call__`` — so ``callable`` alone can't tell a
    *returned* COM object apart from a method we still need to call. The
    ``_oleobj_`` attribute is present on pywin32 dispatch wrappers but not on
    bound methods, so it is the reliable discriminator.
    """
    return hasattr(member, "_oleobj_")
