"""EQL syntax tree nodes/schema."""
from __future__ import unicode_literals

import datetime
import re
from collections import OrderedDict
from operator import lt, le, eq, ne, ge, gt, mul, truediv, mod, add, sub
from string import Template

from .signatures import SignatureMixin
from .types import STRING, BOOLEAN, NUMBER, NULL, PRIMITIVES
from .utils import to_unicode, is_string, is_number, ParserConfig

__all__ = (
    # base classes
    "BaseNode",
    "Expression",
    "EqlNode",

    # Literals
    "Literal",
    "String",
    "Number",
    "Null",
    "Boolean",
    "TimeRange",

    # fields and subfields
    "Field",

    # boolean logic
    "Comparison",
    "InSet",
    "And",
    "Or",
    "Not",
    "FunctionCall",
    "MathOperation",

    # queries
    "EventQuery",
    "NamedSubquery",
    "NamedParams",
    "SubqueryBy",
    "Join",
    "Sequence",

    # pipes
    "PipeCommand",

    # full queries
    "PipedQuery",
    "EqlAnalytic",

    # macros
    "Definition",
    "BaseMacro",
    "CustomMacro",
    "Macro",
    "Constant",
    "PreProcessor",
)


class BaseNode(object):
    """This is the base class for all AST nodes."""

    __slots__ = ()

    template = None  # type: Template
    delims = {}
    precedence = None

    def iter_slots(self):
        # type: () -> list
        """Enumerate over all of the slots and their values."""
        for key in self.__slots__:
            yield key, getattr(self, key, None)

    def optimize(self):
        """Optimize an AST."""
        return self

    def __eq__(self, other):
        """Check if two ASTs are equivalent."""
        return type(self) == type(other) and list(self.iter_slots()) == list(other.iter_slots())

    def __ne__(self, other):
        """Check if two ASTs are not equivalent."""
        return not self == other

    def render(self, precedence=None, **kwargs):
        """Render the AST in the target language."""
        if not self.template:
            raise NotImplementedError()

        dicted = {}
        for name, value in self.iter_slots():
            if isinstance(value, (list, tuple)):
                delim = self.delims[name]
                value = [v.render(self.precedence, **kwargs) if isinstance(v, BaseNode) else v for v in value]
                value = delim.join(v for v in value)
            elif isinstance(value, BaseNode):
                value = value.render(self.precedence, **kwargs)
            dicted[name] = value
        return self.template.substitute(dicted)

    def __repr__(self):
        """Python representation of the AST."""
        return "{}({})".format(type(self).__name__, ", ".join('{}={}'.format(name, repr(slot))
                                                              for name, slot in self.iter_slots()))

    def __iter__(self):
        """Iterate recursively through all nodes in the tree."""
        return Walker().iter_node(self)

    def __unicode__(self):
        """Render the AST back as a valid EQL string."""
        return self.render()

    def __str__(self):
        """Render the AST back as a valid EQL string."""
        unicoded = self.__unicode__()
        # Python 2.7
        if not isinstance(unicoded, str):
            unicoded = unicoded.encode('utf-8')
        return unicoded


# noinspection PyAbstractClass
class EqlNode(BaseNode):
    """The base class for all nodes within the event query language."""

    TAB = '  '
    precedence = None

    def indent(self, text, depth=1):
        """Indent by EQL default tab."""
        delim = self.TAB * depth
        return '\n'.join(delim + line.rstrip() for line in text.splitlines())

    def _render(self):
        # Render the template if defined
        return super(EqlNode, self).render()

    def render(self, precedence=None, **kwargs):
        """Render an EQL node and add parentheses to support orders of operation."""
        rendered = self._render(**kwargs)
        if precedence is not None and self.precedence is not None and self.precedence > precedence:
            return '({})'.format(rendered)
        return rendered


# noinspection PyAbstractClass
class Expression(EqlNode):
    """Base class for expressions."""

    precedence = 0

    def __and__(self, other):
        """Boolean AND between two AST nodes."""
        if isinstance(other, Literal):
            if other.value:
                return self
            return Boolean(False)

        if isinstance(other, And):
            return And([self] + other.terms)
        return And([self, other])

    def __or__(self, other):
        """"Boolean OR between two AST nodes."""
        if isinstance(other, Literal):
            if other.value:
                return Boolean(True)

        if isinstance(other, Or):
            return Or([self] + other.terms)
        return Or([self, other])

    def __invert__(self):
        """Negate an expression with Not."""
        return Not(self)


class Literal(Expression):
    """Static value."""

    __slots__ = 'value',
    precedence = Expression.precedence + 1
    type_hint = PRIMITIVES

    def __init__(self, value):
        """Create an EQL value from a python value."""
        if type(self) is Literal:
            raise TypeError("Literal AST nodes can't be created directly. Try Literal.from_python")
        self.value = value

    @classmethod
    def find_type(cls, python_value):
        """Find the corresponding AST node type for a python value."""
        if python_value is None:
            return Null
        elif python_value is True or python_value is False:
            return Boolean
        elif is_number(python_value):
            return Number
        elif is_string(python_value):
            return String
        else:
            raise TypeError("Unable to convert python value to a literal.")

    @classmethod
    def from_python(cls, python_value):
        """Convert a python value to a literal."""
        subcls = cls.find_type(python_value)
        return subcls(python_value)

    def __and__(self, other):
        """Shortcut ANDing of Static Value nodes together."""
        if isinstance(other, Literal):
            return Boolean(self.value and other.value)
        elif self.value:
            return other
        else:
            return Boolean(False)

    def __or__(self, other):
        """Shortcut ORing of Static Value nodes together."""
        if isinstance(other, Literal):
            return Boolean(self.value or other.value)
        elif self.value:
            return self
        else:
            return other

    def __invert__(self):
        """Negate a static value."""
        return Boolean(not self.value)


class Boolean(Literal):
    """Boolean literal."""

    type_hint = BOOLEAN

    def _render(self):
        return 'true' if self.value else 'false'


class Null(Literal):
    """Null literal."""

    type_hint = NULL

    def __init__(self, value=None):
        """Null literal value."""
        super(Null, self).__init__(None)

    def _render(self):
        return 'null'


class Number(Literal):
    """Numeric literal."""

    type_hint = NUMBER

    def _render(self):
        return to_unicode(self.value)


class String(Literal):
    """String literal."""

    escape_patterns = {
        '\\': '\\\\',
        '\b': '\\b',
        '\t': '\\t',
        '\r': '\\r',
        '\n': '\\n',
        '\f': '\\f',
        '\"': '\\\"',
        '\'': '\\\'',
    }
    reverse_patterns = {v: k for k, v in escape_patterns.items()}
    escape_re = r'[{}]'.format('|'.join(escape_patterns.values()))
    type_hint = STRING

    @classmethod
    def escape(cls, s):
        """Escape known patterns in a string."""
        def replace_callback(sub):
            return cls.escape_patterns.get(sub.group(), sub.group())
        return re.sub(cls.escape_re, replace_callback, s)

    @classmethod
    def unescape(cls, s):
        """Unescape an EQL rendered string."""
        def replace_callback(sub):
            return cls.reverse_patterns.get(sub.group(), sub.group())
        return re.sub(r"\\.", replace_callback, s)

    def _render(self):
        return '"{}"'.format(self.escape(self.value))


class TimeRange(Expression):
    """EQL node for an interval of time."""

    __slots__ = 'delta',
    precedence = Expression.precedence + 1

    def __init__(self, delta):  # type: (datetime.timedelta) -> None
        """EQL time interval."""
        self.delta = delta

    @classmethod
    def convert(cls, node):
        """Convert a StaticValue to a time range."""
        if isinstance(node, TimeRange):
            return node
        elif isinstance(node, Number):
            return TimeRange(datetime.timedelta(seconds=node.value))

    def _render(self):
        interval = self.delta.total_seconds()
        second = 1
        minute = 60 * second
        hour = minute * 60
        day = hour * 24
        decimal = interval
        unit = 's'

        if interval >= day:
            decimal = float(interval) / day
            unit = 'd'
        elif interval >= hour:
            decimal = float(interval) / hour
            unit = 'h'
        elif interval >= minute:
            if interval % minute == 0 or (interval % second) != 0:
                decimal = float(interval) / minute
                unit = 'm'

        # Drop fractional part if it's 0
        if decimal == int(decimal):
            decimal = int(decimal)
        return '{}{}'.format(decimal, unit)


class Field(Expression):
    """Variables and paths in scope of the event."""

    EVENTS = 'events'

    __slots__ = 'base', 'path',
    precedence = Expression.precedence + 1

    def __init__(self, base, path=None):
        """Query the event for the field expression.

        :param str base: The root field
        :param list[str|int] path: The sub fields and array positions
        """
        self.base = base
        self.path = path or []

    def query_multiple_events(self):  # type: () -> (int, Field)
        """Get the index into the event array and query."""
        if self.base == Field.EVENTS and len(self.path) >= 2:
            if is_number(self.path[0]) and is_string(self.path[1]):
                return self.path[0], Field(self.path[1], self.path[2:])
        return 0, self

    @property
    def full_path(self):  # type: () -> list[str]
        """Get the full path for a field."""
        return [self.base] + self.path

    def _render(self):
        text = self.base
        for key in self.path:
            if is_number(key):
                text += "[{}]".format(key)
            else:
                text += ".{}".format(key)
        return text


class FunctionCall(Expression):
    """A call into a user-defined function by name and a list of arguments."""

    __slots__ = 'name', 'arguments', 'as_method'
    precedence = Literal.precedence + 1
    template = Template('$name($arguments)')
    delims = {'arguments': ', '}

    def __init__(self, name, arguments, as_method=False):
        """Call the function by name.

        :param str name: The name of the user-defined function
        :param list[Expression] arguments: Arguments to pass into the function.
        """
        self.name = name
        self.arguments = arguments or []
        self.as_method = as_method

    @property
    def callback(self):
        """Get the callback for this node."""
        return self.signature.get_callback(*self.arguments)

    @property
    def signature(self):
        """Get the matching function signature."""
        return get_function(self.name)

    def optimize(self):
        """Optimize function calls that can be determined at compile time."""
        func = get_function(self.name)
        arguments = [arg.optimize() for arg in self.arguments]

        if func and all(isinstance(arg, Literal) for arg in arguments):
            try:
                rv = func.run(*[arg.value for arg in arguments])
                return Literal.from_python(rv)
            except NotImplementedError:
                pass

        return FunctionCall(self.name, arguments, self.as_method)

    def _render(self):
        """Determine the precedence by checking if it's called as a method."""
        if self.as_method:
            return '{base}:{name}({remaining})'.format(
                base=self.arguments[0].render(self.precedence), name=self.name,
                remaining=", ".join(arg.render(self.precedence) for arg in self.arguments[1:]))

        return super(FunctionCall, self)._render()

    def render(self, precedence=None):
        """Convert wildcards back to the short hand syntax."""
        if self.signature:
            alternate_render = self.signature.alternate_render(self.arguments, precedence)
            if alternate_render:
                return alternate_render

        return super(FunctionCall, self).render()


class NamedSubquery(Expression):
    """Named of queries perform a subquery with a specific type and returns true if the current event is related.

    Query Types:
    - descendant: Returns true if the pid/unique_pid of the event is a descendant of the subquery process
    - child: Returns true if the pid/unique_pid of the event is a child of the subquery process
    - event: Returns true if the pid/unique_pid of the event matches the subquery process
    """

    __slots__ = 'query_type', 'query'
    precedence = FunctionCall.precedence

    DESCENDANT = 'descendant'
    EVENT = 'event'
    CHILD = 'child'

    supported_types = (DESCENDANT, EVENT, CHILD)
    template = Template('$query_type of [$query]')

    def __init__(self, query_type, query):
        """Init.

        :param str query_type: The type of subquery to relate by
        :param EventQuery query: Query applied to the process' ancestor(s)
        """
        self.query_type = query_type
        self.query = query


class MathOperation(Expression):
    """Mathematical operation between two numeric values."""

    __slots__ = 'left', 'operator', 'right'
    OPERATORS = ('*', '/', '%', '+', '-')

    op_lookup = {'*': mul, '/': truediv, '%': mod, '+': add, '-': sub}
    func_lookup = {"*": "multiply", "+": "add", "-": "subtract", "%": "modulo", "/": "divide"}

    min_precedence = NamedSubquery.precedence + 1
    max_precedence = min_precedence + 1
    full_template = Template('$left $operator $right')
    negative_template = Template('$operator$right')

    def __init__(self, left, operator, right):  # type: (Expression, str, Expression) -> None
        """Mathematical operation between two numeric values."""
        self.left = left
        self.operator = operator
        self.right = right

    def to_function_call(self):
        """Convert a math operator to an EQL function call."""
        return FunctionCall(self.func_lookup[self.operator], [self.left, self.right])

    @property
    def precedence(self):
        """Get the precedence depending on the operator."""
        if self.operator in "*/%":
            return self.min_precedence
        else:
            return self.max_precedence

    def optimize(self):
        """Evaluate literals when possible."""
        left = self.left.optimize()
        right = self.right.optimize()

        if isinstance(left, Number) and isinstance(right, Number):
            # don't divide by zero when optimizing, leave that to the target implementation
            if not (right.value == 0 and self.operator in ("/", "%")):
                return Number(self.func(left.value, right.value))

        if isinstance(right, MathOperation) and right.left == Number(0):
            # a +- b parses as a + (0 - b) should become a + -b
            if self.operator in ("-", "+") and right.operator in ("-", "+"):
                operator = "-" if (self.operator == "-") ^ (right.operator == "-") else "+"
                return MathOperation(left, operator, right.right)

        return MathOperation(left, self.operator, right)

    @property
    def template(self):
        """Make the template dynamic."""
        return self.negative_template if self.left == Number(0) else self.full_template

    @property
    def func(self):
        """Get a callback function for the specific operator."""
        return self.op_lookup[self.operator]


class Comparison(Expression):
    """Represents a comparison between two values, as in ``<expr> <comparator> <expr>``.

    Comparison operators include ``==``, ``!=``, ``<``, ``<=``, ``>=``, and ``>``.
    """

    __slots__ = 'left', 'comparator', 'right'
    LT, LE, EQ, NE, GE, GT = ('<', '<=', '==', '!=', '>=', '>')

    func_lookup = {LT: lt, LE: le, EQ: eq, NE: ne, GE: ge, GT: gt}
    precedence = MathOperation.max_precedence + 1
    template = Template('$left $comparator $right')

    def __init__(self, left, comparator, right):
        # type: (Expression, str, Expression) -> None
        """Compare two fields or values to each other."""
        self.left = left
        self.comparator = comparator
        self.right = right
        self.function = self.func_lookup[comparator]

    def __invert__(self):
        """Convert a comparison by flipping the operators."""
        if self.comparator == self.EQ:
            return Comparison(self.left, Comparison.NE, self.right).optimize()
        elif self.comparator == self.NE:
            return Comparison(self.left, Comparison.EQ, self.right).optimize()
        return super(Comparison, self).__invert__()

    def optimize(self):
        """Optimize comparisons against literal values."""
        if isinstance(self.left, Literal) and isinstance(self.right, Literal):
            lhs = self.left.value
            rhs = self.right.value

            # Check that the types match first
            if not isinstance(self.right, type(self.left)):
                return Boolean(self.comparator == Comparison.NE)

            if isinstance(self.left, String):
                lhs = lhs.lower()
                rhs = rhs.lower()

            return Boolean(self.function(lhs, rhs))

        # assumes calling the same function twice with the same args returns the same result
        elif self.left == self.right:
            return Boolean(self.comparator in (Comparison.EQ, Comparison.LE, Comparison.GE))

        return self

    def __or__(self, other):
        """Check for one field being compared to multiple values, and switch to a set."""
        if self.comparator == Comparison.EQ and isinstance(self.right, Literal):
            if isinstance(other, Comparison) and self.left == other.left and other.comparator == Comparison.EQ:
                if isinstance(other.right, Literal):
                    return InSet(self.left, [self.right, other.right])
            elif isinstance(other, InSet) and self.left == other.expression and other.is_literal():
                container = [self.right]
                container.extend(other.container)
                return InSet(self.left, container)
        return super(Comparison, self).__or__(other)

    def __and__(self, other):
        """Check if a comparison is ANDed to a set."""
        if self.comparator == Comparison.EQ and isinstance(other, InSet) and self.left == other.expression:
            return InSet(self.left, [self.right]) & other
        return super(Comparison, self).__and__(other)


class InSet(Expression):
    """Check if the value of a field within an event matches a list of values."""

    __slots__ = 'expression', 'container'
    precedence = Comparison.precedence

    def __init__(self, expression, container):
        # type: (Expression, list[Expression]) -> None
        """Check if a value is in a list of possible values."""
        self.expression = expression
        self.container = container

    def is_literal(self):
        """Check if a set contains only literal values."""
        return all(isinstance(v, Literal) for v in self.container)

    def is_dynamic(self):
        """Check if a set contains only dynamic values."""
        return all(not isinstance(v, Literal) for v in self.container)

    def _get_literals(self):
        """Get the values in the set."""
        values = OrderedDict()

        for literal in self.container:  # type: Literal
            if not isinstance(literal, Literal):
                continue
            k = literal.value
            if isinstance(literal, String):
                values.setdefault(k.lower(), literal)
            else:
                values[k] = literal
        return values

    def __and__(self, other):
        """Perform an intersection between two sets for boolean AND."""
        if isinstance(other, InSet) and self.expression == other.expression:
            if self.is_literal() and other.is_literal():
                container1 = self._get_literals()
                container2 = other._get_literals()

                reduced = [v for k, v in container1.items() if k in container2]
                return InSet(self.expression, reduced).optimize()

        elif isinstance(other, Not):
            if isinstance(other.term, InSet) and self.expression == other.term.expression:
                # Check if one set is being subtracted from another
                if self.is_literal() and other.term.is_literal():
                    container1 = self._get_literals()
                    container2 = other.term._get_literals()

                    reduced = [v for k, v in container1.items() if k not in container2]
                    return InSet(self.expression, reduced).optimize()

        elif isinstance(other, Comparison) and other.comparator == Comparison.EQ and self.expression == other.left:
            if self.is_literal() and isinstance(other.right, Literal):
                return super(InSet, self).__and__(InSet(other.left, [other.right])).optimize()

        elif isinstance(other, Comparison) and other.comparator == Comparison.NE and self.expression == other.left:
            if self.is_literal() and isinstance(other.right, Literal):
                return super(InSet, self).__and__(~ InSet(other.left, [other.right])).optimize()

        return super(InSet, self).__and__(other)

    def __or__(self, other):
        """Perform a union between two sets for boolean OR."""
        if isinstance(other, InSet) and self.expression == other.expression:
            if self.is_literal() and other.is_literal():
                container = self._get_literals()
                for k, v in other._get_literals().items():
                    container.setdefault(k, v)

                union = [v for v in container.values()]
                return InSet(self.expression, union).optimize()

        elif isinstance(other, Comparison) and self.expression == other.left:
            if self.is_literal() and isinstance(other.right, Literal):
                return super(InSet, self).__or__(InSet(other.left, [other.right]))

        return super(InSet, self).__or__(other)

    def split_literals(self):
        """Split the set lookup into static values and dynamic values."""
        if self.is_dynamic() or self.is_literal():
            return self

        literals = InSet(self.expression, [])
        dynamic = InSet(self.expression, [])
        for item in self.container:
            if isinstance(item, Literal):
                literals.container.append(item)
            else:
                dynamic.container.append(item)

        return literals.optimize() | dynamic.optimize()

    def optimize(self):
        """Optimize the AST."""
        expression = self.expression

        # move all the literals to the front, preserve their ordering
        literals = [v for k, v in self._get_literals().items()]
        dynamic = [v for v in self.container if not isinstance(v, Literal)]
        container = literals + dynamic

        # check to see if a literal value is in the list of literal values
        if isinstance(self.expression, Literal):
            value = self.expression.value
            if is_string(value):
                value = value.lower()
            if value in self._get_literals():
                return Boolean(True)
            container = dynamic

        if len(container) == 0:
            return Boolean(False)
        elif len(container) == 1:
            return Comparison(expression, Comparison.EQ, container[0]).optimize()
        elif expression in container:
            return Boolean(True)

        return InSet(expression, container)

    @property
    def synonym(self):
        """Get an equivalent node that does performs multiple comparisons with 'or' and '=='."""
        return Or([Comparison(self.expression, Comparison.EQ, v) for v in self.container])

    def _render(self, negate=False):
        values = [v.render() for v in self.container]
        expr = self.expression.render(self.precedence)
        operator = 'not in' if negate else 'in'

        if len(self.container) > 3 and sum(len(v) for v in values) > 40:
            delim = ',\n'
            return '{lhs} {op} (\n{rhs}\n)'.format(lhs=expr, op=operator, rhs=self.indent(delim.join(values)))
        else:
            delim = ', '
            return '{lhs} {op} ({rhs})'.format(lhs=expr, op=operator, rhs=delim.join(values))


class BaseCompound(Expression):
    """Combine multiple expressions with a single operator."""

    __slots__ = 'terms',
    operator = None  # type: str

    def __init__(self, terms):
        """Combine multiple expressions with an operator.

        :param list[Expression] terms: List of terms
        """
        self.terms = terms

    def _render(self):
        scoped_terms = [term.render(self.precedence) for term in self.terms]
        if len(scoped_terms) == 1:
            return scoped_terms[0]

        if len(self.terms) > 4 or any(isinstance(t, (BaseCompound, NamedSubquery, InSet)) for t in self.terms):
            delim = ' {}\n'.format(self.operator)
            indented = [self.indent(t) for t in scoped_terms]
            return delim.join(indented).lstrip()
        else:
            delim = ' {} '.format(self.operator)
            return delim.join(scoped_terms).lstrip()


class Not(Expression):
    """Negate a boolean expression."""

    __slots__ = 'term',
    precedence = Comparison.precedence + 1
    template = Template('not $term')

    def __init__(self, term):
        """Init.

        :param Expression term: The query node to negate
        """
        self.term = term

    def demorgans(self):
        """Apply DeMorgan's law."""
        if isinstance(self, Or):
            return And([~ t for t in self.terms]).optimize()

        elif isinstance(self, And):
            return Or([~ t for t in self.terms]).optimize()

        else:
            return ~ self.term.optimize()

    def optimize(self):
        """Optimize NOT terms, by flattening them."""
        optimized_term = self.term.optimize()
        return ~ optimized_term

    def __invert__(self):
        """Convert ``not not X`` to X."""
        return self.term.optimize()

    def render(self, precedence=None):
        """Convert wildcard functions back to the short hand syntax."""
        if isinstance(self.term, InSet):
            return self.term.render(precedence, negate=True)

        if isinstance(self.term, FunctionCall) and self.term.name == 'wildcard':
            if len(self.term.arguments) == 2 and isinstance(self.term.arguments[1], String):
                lhs, rhs = self.term.arguments
                return Comparison(lhs, Comparison.NE, rhs).render(precedence)
        return super(Not, self).render(precedence)


class And(BaseCompound):
    """Perform a boolean ``and`` on a list of expressions."""

    precedence = Not.precedence + 1
    operator = 'and'

    def optimize(self):
        """Optimize AND terms, by flattening them."""
        terms = []
        current = self.terms[0]
        for term in self.terms[1:]:
            current = current & term
            if isinstance(current, And):
                terms.extend(current.terms[:-1])
                current = current.terms[-1]

        if terms:
            terms.append(current)
            return And(terms)
        return current

    def __and__(self, other):
        """Flatten multiple ``and`` terms."""
        terms = self.terms
        if isinstance(other, And):
            terms.extend(other.terms)
        else:
            terms.append(other)
        return And(terms)


class Or(BaseCompound):
    """Perform a boolean ``or`` on a list of expressions."""

    precedence = And.precedence + 1
    operator = 'or'

    def optimize(self):
        """Optimize OR terms, by flattening them."""
        terms = []
        current = self.terms[0]
        for term in self.terms[1:]:
            current = current | term
            if isinstance(current, Or):
                terms.extend(current.terms[:-1])
                current = current.terms[-1]

        if terms:
            terms.append(current)
            return Or(terms)
        return current

    def __or__(self, other):
        """Flatten multiple ``or`` terms."""
        terms = self.terms
        if isinstance(other, Or):
            terms.extend(other.terms)
        else:
            terms.append(other)
        return Or(terms)


class EventQuery(EqlNode):
    """Query over a specific event type with a boolean condition."""

    __slots__ = 'event_type', 'query'
    template = Template('$event_type where $query')

    def __init__(self, event_type, query):
        """Init.

        :param str event_type: One of the event types in the repo sensor/eventing_schema
        :param query: The query scoped to the event type
        """
        self.event_type = event_type
        self.query = query

    def _render(self):
        query_text = self.query.render()
        if '\n' in query_text:
            return '{} where\n{}'.format(self.event_type, self.indent(query_text))

        return super(EventQuery, self)._render()


class NamedParams(EqlNode):
    """An EQL node for key-value named parameters."""

    __slots__ = 'kv',

    def __init__(self, kv=None):
        """Key value store for EQL parameters.

        :param dict[str, Expression] kv: The named key-value parameters.
        """
        self.kv = kv or {}

    def _render(self):
        return ' '.join('{}={}'.format(k, v.render(Literal.precedence)) for k, v in self.kv.items())


class SubqueryBy(EqlNode):
    """Node for holding the :class:`~EventQuery` and parameters to join on."""

    __slots__ = 'query', 'params', 'join_values'

    def __init__(self, query, params=None, join_values=None):
        """Init.

        :param EventQuery query: The event query enclosed in the term
        :param NamedParams params: The parameters for the query.
        :param list[Expression] join_values: The field to join values on
        """
        self.query = query
        self.params = params or NamedParams()
        self.join_values = join_values or []

    def _render(self):
        text = "[{}]".format(self.query.render())
        params = self.params.render()
        if len(params):
            text += ' ' + params

        if len(self.join_values):
            text += ' by {}'.format(', '.join(jv.render() for jv in self.join_values))
        return text


class Join(EqlNode):
    """Another boolean query that can join multiple events that share common values."""

    __slots__ = 'queries', 'close'

    def __init__(self, queries, close=None):
        """Init.

        :param list[SubqueryBy] queries:
        :param SubqueryBy close: The condition to purge all join state.
        """
        self.queries = queries
        self.close = close

    def _render(self):
        text = 'join\n'
        text += self.indent('\n'.join(query.render() for query in self.queries))

        if self.close:
            text += '\nuntil\n' + self.indent(self.close.render())
        return text


class Sequence(EqlNode):
    """Sequence is very similar to join, but enforces an ordering.

    Sequence supports the ``until`` keyword, which indicates an event that causes it to terminate early.
    """

    __slots__ = 'queries', 'params', 'close'

    def __init__(self, queries, params=None, close=None):
        """Create a Sequence of multiple events.

        :param list[SubqueryBy] queries: List of queries to be sequenced
        :param NamedParams params: Dictionary of timing parameters for the sequence.
        :param SubqueryBy close: An optional query that causes all sequence state to expire
        """
        self.queries = queries
        self.params = params or NamedParams()
        self.close = close

    def _render(self):
        text = 'sequence'
        params = self.params.render()
        if params:
            text += ' with {}'.format(self.params.render())
        text += '\n'
        text += self.indent('\n'.join(query.render() for query in self.queries))

        if self.close:
            text += '\nuntil\n' + self.indent(self.close.render())
        return text


# noinspection PyAbstractClass
class PipeCommand(EqlNode, SignatureMixin):
    """Base class for an EQL pipe."""

    __slots__ = 'arguments',
    name = None  # type: str
    lookup = {}  # type: dict[str, PipeCommand|type]

    def __init__(self, arguments=None):  # type: (list[Expression]) -> None
        """Create a pipe with optional arguments."""
        self.arguments = arguments or []
        super(PipeCommand, self).__init__()

    @classmethod
    def register(cls, name):
        """Register a pipe class by name."""
        def decorator(pipe_class):
            pipe_class.name = name
            if name in cls.lookup:
                raise KeyError("Pipe {} already registered as {}".format(cls.lookup[name], name))
            cls.lookup[name] = pipe_class
            return pipe_class
        return decorator

    @classmethod
    def output_schemas(cls, arguments, type_hints, event_schemas):
        # type: (list, list, list[Schema]) -> list[Schema]
        """Output a list of schemas for each event in the pipe."""
        return event_schemas

    def _render(self):
        if len(self.arguments) == 0:
            return self.name
        return self.name + ' ' + ', '.join(arg.render() for arg in self.arguments)


class PipedQuery(EqlNode):
    """List of all the pipes."""

    __slots__ = 'first', 'pipes'

    def __init__(self, first, pipes=None):
        """Init.

        :param EventQuery|Join|Sequence first: first query
        :param list[PipeCommand] pipes: List of all of the following pipes
        """
        self.first = first
        self.pipes = pipes or []

    def _render(self):
        all_pipes = [self.first] + self.pipes
        return '\n| '.join(pipe.render() for pipe in all_pipes)


class EqlAnalytic(EqlNode):
    """Analytics are the top-level nodes for matching and returning events."""

    __slots__ = 'query', 'metadata'

    def __init__(self, query, metadata=None):
        """Init.

        :param PipedQuery query: Analytic query
        :param dict metadata: Metadata for the analytic
        """
        self.query = query
        self.metadata = metadata or {}

    @property
    def id(self):
        """Return the ID from metadata."""
        return self.metadata.get('id')

    @property
    def name(self):
        """Return the name from metadata."""
        return self.metadata.get('name')

    def __unicode__(self):
        """Print a string instead of the dictionary that render returns."""
        return self.query.__unicode__()

    def __str__(self):
        """Print a string instead of the dictionary that render returns."""
        return self.query.__str__()

    def _render(self):
        return {'metadata': self.metadata, 'query': self.query.render()}


class Definition(object):
    """EQL definitions used for pre-processor expansion."""

    __slots__ = 'name',

    def __init__(self, name):
        """Create a generic definition with a name.

        :param str name: The name of the macro
        """
        self.name = name
        super(Definition, self).__init__()


class Constant(Definition, EqlNode):
    """EQL constant which binds a literal to a name."""

    __slots__ = 'name', 'value',
    template = Template('const $name = $value')

    def __init__(self, name, value):  # type: (str, Literal) -> None
        """Create an EQL literal constant."""
        super(Constant, self).__init__(name)
        self.value = value


class BaseMacro(Definition):
    """Base macro class."""

    def expand(self, arguments):
        """Expand a macro with a set of arguments."""
        raise NotImplementedError


class CustomMacro(BaseMacro):
    """Custom macro class to use Python callbacks to transform trees."""

    def __init__(self, name, callback):
        """Python macro to allow for more dynamic or sophisticated macros.

        :param str name: The name of the macro.
        :param (list[EqlNode]) -> EqlNode callback: A callback to expand out the macro.
        """
        super(CustomMacro, self).__init__(name)
        self.callback = callback

    def expand(self, arguments):
        """Make the callback do the dirty work for expanding the AST."""
        node = self.callback(arguments)
        return node.optimize()

    @classmethod
    def from_name(cls, name):
        """Decorator to convert a function into a :class:`~CustomMacro` object."""
        def decorator(f):
            return CustomMacro(name, f)
        return decorator


class Macro(BaseMacro, EqlNode):
    """Class for a macro on a node, to allow for client-side expansion."""

    __slots__ = 'name', 'parameters', 'expression'
    template = Template('macro $name($parameters) $expression')
    delims = {'parameters': ', '}

    def __init__(self, name, parameters, expression):
        """Create a named macro that takes a list of arguments and returns a paramaterized expression.

        :param str name: The name of the macro.
        :param list[str]: The names of the parameters.
        :param Expression expression: The parameterized expression to return.
        """
        BaseMacro.__init__(self, name)
        EqlNode.__init__(self)
        self.parameters = parameters
        self.expression = expression

    def expand(self, arguments):
        """Expand a node.

        :param list[BaseNode node] arguments: The arguments the macro is called with
        :param Walker walker: An optional syntax tree walker.
        :param bool optimize: Return an optimized copy of the AST
        :rtype: BaseNode
        """
        if len(arguments) != len(self.parameters):
            raise ValueError("Macro {} expected {} arguments but received {}".format(
                self.name, len(self.parameters), len(arguments)))

        lookup = dict(zip(self.parameters, arguments))

        def _walk_field(node):
            if node.base in lookup and not node.path:
                return lookup[node.base].optimize()
            return node

        walker = RecursiveWalker()
        walker.register_func(Field, _walk_field)
        return walker.walk(self.expression).optimize()

    def _render(self):
        expr = self.expression.render()
        if '\n' in expr or len(expr) > 40:
            expr = '\n' + self.indent(expr)
            return self.template.substitute(name=self.name, parameters=', '.join(self.parameters), expression=expr)
        return super(Macro, self)._render()


class PreProcessor(ParserConfig):
    """An EQL preprocessor stores definitions and is used for macro expansion and constants."""

    def __init__(self, definitions=None):
        """Initialize a preprocessor environment that can load definitions."""
        self.constants = OrderedDict()  # type: dict[str, Constant]
        self.macros = OrderedDict()  # type: dict[str, BaseMacro|CustomMacro|Maco]

        class PreProcessorWalker(RecursiveWalker):
            """Custom walker class for this preprocessor."""

            preprocessor = self

            def _walk_field(self, node, *args, **kwargs):
                if node.base in self.preprocessor.constants and not node.path:
                        return self.preprocessor.constants[node.base].value
                return self._walk_base_node(node, *args, **kwargs)

            def _walk_function_call(self, node, *args, **kwargs):
                if node.name in self.preprocessor.macros:
                    macro = self.preprocessor.macros[node.name]
                    arguments = [self.walk(arg, *args, **kwargs) for arg in node.arguments]
                    return macro.expand(arguments)
                return self._walk_base_node(node, *args, **kwargs)

        self.walker_cls = PreProcessorWalker
        ParserConfig.__init__(self, preprocessor=self)
        self.add_definitions(definitions or [])

    def add_definitions(self, definitions):
        """Add a list of definitions."""
        for definition in definitions:
            self.add_definition(definition)

    def add_definition(self, definition):  # type: (BaseMacro|Constant) -> None
        """Add a named definition to the preprocessor."""
        name = definition.name
        if isinstance(definition, BaseMacro):
            # The macro may call into other macros so it should be expanded
            expanded_macro = self.expand(definition)
            self.macros[name] = expanded_macro
        elif isinstance(definition, Constant):
            if name in self.constants:
                raise KeyError("Constant {} already defined".format(name))
            self.constants[name] = definition

    def expand(self, root):
        """Expand the function calls that match registered macros.

        :param EqlNode root: The input node, macro, expression, etc.
        :param bool optimize: Toggle AST optimizations while expanding
        :rtype: EqlNode
        """
        if not self.constants and not self.macros:
            return root

        return self.walker_cls().walk(root)

    def copy(self):
        """Create a shallow copy of a preprocessor."""
        preprocessor = PreProcessor()
        preprocessor.constants.update(self.constants)
        preprocessor.macros.update(self.macros)
        return preprocessor


# circular dependency
from .walkers import Walker, RecursiveWalker  # noqa: E402
from .functions import get_function  # noqa: E402
