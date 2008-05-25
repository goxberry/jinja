# -*- coding: utf-8 -*-
"""
    jinja2.sandbox
    ~~~~~~~~~~~~~~

    Adds a sandbox layer to Jinja as it was the default behavior in the old
    Jinja 1 releases.  This sandbox is slightly different from Jinja 1 as the
    default behavior is easier to use.

    The behavior can be changed by subclassing the environment.

    :copyright: Copyright 2008 by Armin Ronacher.
    :license: BSD.
"""
import operator
from UserDict import UserDict, DictMixin
from UserList import UserList
from sets import Set, ImmutableSet
from types import FunctionType, MethodType, TracebackType, CodeType, \
     FrameType, GeneratorType
from jinja2.runtime import Undefined
from jinja2.environment import Environment
from jinja2.exceptions import SecurityError


#: maximum number of items a range may produce
MAX_RANGE = 100000

#: attributes of function objects that are considered unsafe.
UNSAFE_FUNCTION_ATTRIBUTES = set(['func_closure', 'func_code', 'func_dict',
                                  'func_defaults', 'func_globals'])

#: unsafe method attributes.  function attributes are unsafe for methods too
UNSAFE_METHOD_ATTRIBUTES = set(['im_class', 'im_func', 'im_self'])

SET_TYPES = (ImmutableSet, Set, set)
MODIFYING_SET_ATTRIBUTES = set([
    'add', 'clear', 'difference_update', 'discard', 'pop', 'remove',
    'symmetric_difference_update', 'update'
])

DICT_TYPES = (UserDict, DictMixin, dict)
MODIFYING_DICT_ATTRIBUTES = set(['clear', 'pop', 'popitem', 'setdefault',
                                 'update'])

LIST_TYPES = (UserList, list)
MODIFYING_LIST_ATTRIBUTES = set(['append', 'reverse', 'insert', 'sort',
                                 'extend', 'remove'])


def safe_range(*args):
    """A range that can't generate ranges with a length of more than
    MAX_RANGE items.
    """
    rng = xrange(*args)
    if len(rng) > MAX_RANGE:
        raise OverflowError('range too big, maximum size for range is %d' %
                            MAX_RANGE)
    return rng


def unsafe(f):
    """
    Mark a function or method as unsafe::

        @unsafe
        def delete(self):
            pass
    """
    f.unsafe_callable = True
    return f


def is_internal_attribute(obj, attr):
    """Test if the attribute given is an internal python attribute.  For
    example this function returns `True` for the `func_code` attribute of
    python objects.  This is useful if the environment method
    :meth:`~SandboxedEnvironment.is_safe_attribute` is overriden.

    >>> from jinja2.sandbox import is_internal_attribute
    >>> is_internal_attribute(lambda: None, "func_code")
    True
    >>> is_internal_attribute((lambda x:x).func_code, 'co_code')
    True
    >>> is_internal_attribute(str, "upper")
    False
    """
    if isinstance(obj, FunctionType):
        return attr in UNSAFE_FUNCTION_ATTRIBUTES
    if isinstance(obj, MethodType):
        return attr in UNSAFE_FUNCTION_ATTRIBUTES or \
               attr in UNSAFE_METHOD_ATTRIBUTES
    if isinstance(obj, type):
        return attr == 'mro'
    if isinstance(obj, (CodeType, TracebackType, FrameType)):
        return True
    if isinstance(obj, GeneratorType):
        return attr == 'gi_frame'
    return attr.startswith('__')


def modifies_builtin_mutable(obj, attr):
    """This function checks if an attribute on a builtin mutable object
    (list, dict or set) would modify it if called.  It also supports
    the "user"-versions of the objects (`sets.Set`, `UserDict.*` etc.)

    >>> modifies_builtin_mutable({}, "clear")
    True
    >>> modifies_builtin_mutable({}, "keys")
    False
    >>> modifies_builtin_mutable([], "append")
    True
    >>> modifies_builtin_mutable([], "index")
    False

    If called with an unsupported object (such as unicode) `False` is
    returned.

    >>> modifies_builtin_mutable("foo", "upper")
    False
    """
    if isinstance(obj, LIST_TYPES):
        return attr in MODIFYING_LIST_ATTRIBUTES
    elif isinstance(obj, DICT_TYPES):
        return attr in MODIFYING_DICT_ATTRIBUTES
    elif isinstance(obj, SET_TYPES):
        return attr in MODIFYING_SET_ATTRIBUTES
    return False


class SandboxedEnvironment(Environment):
    """The sandboxed environment.  It works like the regular environment but
    tells the compiler to generate sandboxed code.  Additionally subclasses of
    this environment may override the methods that tell the runtime what
    attributes or functions are safe to access.

    If the template tries to access insecure code a :exc:`SecurityError` is
    raised.  However also other exceptions may occour during the rendering so
    the caller has to ensure that all exceptions are catched.
    """
    sandboxed = True

    def __init__(self, *args, **kwargs):
        Environment.__init__(self, *args, **kwargs)
        self.globals['range'] = safe_range

    def is_safe_attribute(self, obj, attr, value):
        """The sandboxed environment will call this method to check if the
        attribute of an object is safe to access.  Per default all attributes
        starting with an underscore are considered private as well as the
        special attributes of internal python objects as returned by the
        :func:`is_internal_attribute` function.
        """
        return not (attr.startswith('_') or is_internal_attribute(obj, attr))

    def is_safe_callable(self, obj):
        """Check if an object is safely callable.  Per default a function is
        considered safe unless the `unsafe_callable` attribute exists and is
        True.  Override this method to alter the behavior, but this won't
        affect the `unsafe` decorator from this module.
        """
        return not (getattr(obj, 'unsafe_callable', False) or \
                    getattr(obj, 'alters_data', False))

    def subscribe(self, obj, argument):
        """Subscribe an object from sandboxed code."""
        is_unsafe = False
        if isinstance(argument, basestring):
            try:
                attr = str(argument)
            except:
                pass
            else:
                try:
                    value = getattr(obj, attr)
                except AttributeError:
                    pass
                else:
                    if self.is_safe_attribute(obj, argument, value):
                        return value
                    is_unsafe = True
        try:
            return obj[argument]
        except (TypeError, LookupError):
            if is_unsafe:
                return self.undefined('access to attribute %r of %r object is'
                                      ' unsafe.' % (
                    argument,
                    obj.__class__.__name__
                ), name=argument, exc=SecurityError)
        return self.undefined(obj=obj, name=argument)

    def call(__self, __context, __obj, *args, **kwargs):
        """Call an object from sandboxed code."""
        # the double prefixes are to avoid double keyword argument
        # errors when proxying the call.
        if not __self.is_safe_callable(__obj):
            raise SecurityError('%r is not safely callable' % (__obj,))
        return __context.call(__obj, *args, **kwargs)


class ImmutableSandboxedEnvironment(SandboxedEnvironment):
    """Works exactly like the regular `SandboxedEnvironment` but does not
    permit modifications on the builtin mutable objects `list`, `set`, and
    `dict` by using the :func:`modifies_builtin_mutable` function.
    """

    def is_safe_attribute(self, obj, attr, value):
        if not SandboxedEnvironment.is_safe_attribute(self, obj, attr, value):
            return False
        return not modifies_builtin_mutable(obj, attr)