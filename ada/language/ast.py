from __future__ import absolute_import, division, print_function

from langkit.dsl import (
    AnalysisUnitKind, AnalysisUnitType, ASTNode, BoolType, EnumNode,
    EquationType, Field, LexicalEnvType, LogicVarType, LongType, Struct,
    Symbol, T, UserField, abstract, synthetic, env_metadata,
    has_abstract_list, Annotations
)
from langkit.envs import EnvSpec, RefEnvs, add_to_env
from langkit.expressions import (
    AbstractKind, AbstractProperty, And, Bind, DynamicVariable, EmptyArray,
    EmptyEnv, EnvGroup, If, Let, Literal, New, No, Not, Or, Property, Self,
    Var, ignore, langkit_property
)
from langkit.expressions.analysis_units import UnitBody, UnitSpecification
from langkit.expressions.logic import Predicate, LogicTrue


env = DynamicVariable('env', LexicalEnvType)
origin = DynamicVariable('origin', T.AdaNode)


def ref_used_packages():
    """
    If Self is a library item, reference the environments for
    packages that are used at the top-level here. See
    UsePackageClause's ref_env_nodes for the rationale.
    """
    return RefEnvs(T.Expr.designated_env_wrapper,
                   Self.library_item_use_package_clauses)


def ref_std():
    """
    Make the Standard package automatically used.
    """
    return RefEnvs(AdaNode.std_env, Self.self_library_item_or_none)


def ref_generic_formals():
    """
    If Self is a generic package/subprogram and not a library item,
    then the generic formals are not available in parent
    environments. Make them available with ref_envs.
    """
    return RefEnvs(T.AdaNode.generic_formal_env_of_not_library_item,
                   Self.cast(T.AdaNode).to_array)


def add_to_env_kv(key, val, *args, **kwargs):
    """
    Wrapper around envs.add_to_env, that takes a key and a val expression, and
    creates the intermediate env_assoc Struct.
    """
    return add_to_env(
        New(T.env_assoc, key=key, val=val), *args, **kwargs
    )


def env_mappings(base_id_list, entity):
    """
    Creates an env mapping array from a list of BaseId to be used as keys, and
    an entity to be used as value in the mappings.
    """
    return base_id_list.map(
        lambda base_id: New(T.env_assoc, key=base_id.sym, val=entity)
    )


def get_library_item(unit):
    """
    Property helper to get the library unit corresponding to "unit".
    """
    return unit.root.then(
        lambda root:
            root.cast_or_raise(T.CompilationUnit).body
                .cast_or_raise(T.LibraryItem).item
    )


def canonical_type_or_null(type_expr):
    """
    If "type_expr" is null, return null, otherwise return its canonical type
    declaration.
    """
    return type_expr._.designated_type.canonical_type


@env_metadata
class Metadata(Struct):
    dottable_subp = UserField(
        BoolType, doc="Whether the stored element is a subprogram accessed "
                      "through the dot notation"
    )
    implicit_deref = UserField(
        BoolType, doc="Whether the stored element is accessed through an "
                      "implicit dereference"
    )


@abstract
class AdaNode(ASTNode):
    """
    Root node class for the Ada syntax tree.
    """

    annotations = Annotations(
        generic_list_type='AdaList',
        warn_on_node=True
    )

    type_val = Property(
        No(T.AdaNode.entity),
        public=True,
        doc="""
        This will return the value of the type of this node after symbol
        resolution. NOTE: For this to be bound, resolve_names needs to be
        called on the appropriate parent node first.
        """
    )
    ref_val = Property(
        No(T.AdaNode.entity),
        public=True,
        doc="""
        This will return the node this nodes references after symbol
        resolution. NOTE: For this to be bound, resolve_names needs to be
        called on the appropriate parent node first.
        """
    )

    @langkit_property(return_type=EquationType, dynamic_vars=[env, origin])
    def xref_equation():
        """
        This is the base property for constructing equations that, when solved,
        will resolve names and types for every sub expression of the expression
        you call it on. Note that if you call that on any expression, in some
        context it might lack full information and return multiple solutions.
        If you want completely precise resolution, you must call that on the
        outermost node that supports xref_equation.
        """
        # TODO: Maybe this should eventually be an AbstractProperty, but during
        # the development of the xref engine, it is practical to have the
        # default implementation return null, so that we can fail gracefully.
        return No(EquationType)

    xref_stop_resolution = Property(False)

    @langkit_property(return_type=EquationType, dynamic_vars=[env, origin])
    def sub_equation():
        """
        Wrapper for xref_equation, meant to be used inside of xref_equation
        when you want to get the sub equation of a sub expression. It is
        used to change the behavior when xref_equation is called from
        another xref_equation call, or from the top level, so that we can do
        resolution in several steps.
        """
        return If(Self.xref_stop_resolution,
                  LogicTrue(),
                  Self.xref_equation)

    @langkit_property(return_type=BoolType, dynamic_vars=[env, origin])
    def resolve_names_internal(initial=BoolType):
        """
        Internal helper for resolve_names, implementing the recursive logic.
        """
        i = Var(If(initial | Self.xref_stop_resolution,
                   Self.xref_equation._.solve,
                   True))

        j = Self.children.all(lambda c: c.then(
            lambda c: c.resolve_names_internal(False), default_val=True
        ))
        return i & j

    xref_entry_point = Property(
        False,
        public=True,
        doc="""
        Designates entities that are entry point for the xref solving
        infrastructure. If this returns true, then resolve_names can be called
        on it.
        """
    )

    @langkit_property(return_type=BoolType, public=True)
    def resolve_names():
        """
        This will resolve names for this node. If the operation is successful,
        then type_var and ref_var will be bound on appropriate subnodes of the
        statement.
        """
        return env.bind(Self.node_env,
                        origin.bind(Self, Self.resolve_names_internal(True)))

    # TODO: Navigation properties are not ready to deal with units containing
    # multiple packages.

    body_unit = Property(
        get_library_item(Self.unit)._.match(
            lambda body=T.Body: body.unit,
            lambda decl=T.BasicDecl:
                decl.defining_name.referenced_unit(UnitBody),
        ),

        public=True, doc="""
        If this unit has a body, fetch and return it.
        """
    )

    spec_unit = Property(
        get_library_item(Self.unit)
        .cast(T.Body)._.defining_name.referenced_unit(UnitSpecification),

        public=True, doc="""
        If this unit has a spec, fetch and return it. Return the null analysis
        unit otherwise. Note that this returns null for specs, as they don't
        have another spec themselves.
        """
    )

    parent_unit_spec = Property(
        get_library_item(Self.unit)._.defining_name.cast(T.DottedName)
        ._.referenced_unit(UnitSpecification),

        public=True, doc="""
        If this unit is a spec and is a child unit, return the spec of the
        parent unit. Return a null analysis unit for all other cases.
        """
    )

    std = Property(
        # This property is used during referenced envs resolution. As a
        # consequence, a recursive env lookup here would yield infinite
        # recursion, as all recursive env lookups will eventually evaluate
        # this. We know that Standard is available without any use clause
        # anyway, so non-recursive lookup is fine.
        Self.unit.root.node_env.get('Standard', recursive=False).at(0),
        doc="""
        Retrieves the standard unit. Used to access standard types.
        """
    )

    std_env = Property(
        Self.std.children_env,
        doc="""
        Get the children env of the Standard package.
        """
    )

    std_entity = Property(
        lambda sym=Symbol: Self.std_env.get(sym).at(0),
        doc="Return an entity from the standard package with name `sym`"
    )

    bool_type = Property(Self.std_entity('Boolean'))

    @langkit_property()
    def has_with_visibility(refd_unit=AnalysisUnitType):
        """
        Return whether Self's unit has "with visibility" on "refd_unit".

        In other words, whether Self's unit has a WITH clause on "refd_unit",
        or if its spec, or one of its parent specs has one.
        """
        return (
            refd_unit.is_referenced_from(Self.unit)
            | Self.unit.root.spec_unit.then(
                lambda u: refd_unit.is_referenced_from(u)
            ) | Self.unit.root.parent_unit_spec.then(
                lambda u: refd_unit.is_referenced_from(u)
            )
        )

    @langkit_property()
    def resolve_generic_actual():
        """
        Helper property to resolve the actuals of generic instantiations.
        """
        return Self.as_entity.match(
            lambda te=T.TypeExpr.entity: origin.bind(Self, te.designated_type),

            # TODO: depending on the formal that matches this actual, this name
            # can be both an object or a type. For now, we assume it's a type
            # but we should handle objects too.
            lambda n=T.Name.entity: n.name_designated_type,

            lambda _: No(T.entity),
        )

    @langkit_property()
    def library_item_use_package_clauses():
        """
        If Self is a library item, return a flat list of all names for
        top-level UsePackageClause nodes. See
        UsePackageClause.env_spec.ref_envs for more details.
        """
        lib_item = Var(Self.get_parent_library_item)
        return If(
            Self.as_entity.equals(lib_item),

            lib_item.parent.parent.cast_or_raise(T.CompilationUnit)
            .prelude
            .filter(lambda p: p.is_a(UsePackageClause))
            .mapcat(
                lambda p: p.cast_or_raise(UsePackageClause).packages.map(
                    lambda n: n.cast(AdaNode)
                )
            ),

            EmptyArray(AdaNode)
        )

    @langkit_property()
    def self_library_item_or_none():
        """
        Helper for Standard package automatic "use". If Self is a library item,
        return a singleton array for Self. Otherwise, return an empty array.
        """
        return If(Self.parent.is_a(T.LibraryItem),
                  Self.to_array,
                  EmptyArray(AdaNode))

    @langkit_property()
    def generic_formal_env_of_not_library_item():
        """
        Assuming Self is a generic package (or subprogram) body that is not a
        library item, return the lexical environment for the corresponding
        GenericPackageDecl (or GenericSubpDecl) node. Return an empty
        environment in all other cases.

        This is a helper for generic formals visibility in generic bodies. See
        the use in the child_unit macro.
        """
        # TODO: refine this property to preserve entities when it makes sense
        gen_decl = Var(If(
            Self.is_library_item,

            No(T.AdaNode.entity),

            Self.match(
                lambda pkg_body=T.PackageBody:
                    pkg_body.decl_part._.parent.cast(T.GenericPackageDecl)
                    .as_entity,
                lambda subp_body=T.SubpBody:
                    # If subp_body is the body of a generic subprogram, then
                    # the environment lookup below should return its
                    # specification as the second node (the first being
                    # subp_body). If it's not a generic declaration, then we
                    # know subp_body is not a generic subprogram, and thus we
                    # must return a null node here.
                    subp_body.parent.node_env.get(
                        subp_body.relative_name
                    ).at(1).cast(T.GenericSubpDecl).cast(T.AdaNode),
                lambda _: No(T.AdaNode.entity)
            )
        ))
        return gen_decl._.children_env

    @langkit_property()
    def is_library_item():
        """
        Property helper to determine if an entity is the root entity for its
        unit.
        """
        return Self.parent.then(lambda p: p.match(
            lambda _=T.LibraryItem: True,
            lambda gen_pkg_decl=T.GenericPackageDecl:
                gen_pkg_decl.parent.then(lambda p: p.is_a(LibraryItem)),
            lambda _: False,
        ))

    @langkit_property()
    def get_parent_library_item():
        """
        Property helper to return the first parent whose own parent is a
        LibraryItem node.
        """
        return (
            Self.parents.filter(lambda p: p.is_a(T.LibraryItem))
            .at(0).cast(T.LibraryItem).item.as_entity
        )

    @langkit_property()
    def is_package():
        """
        Property helper to determine if an entity is a package or not.
        """
        return Self.is_a(PackageDecl, PackageBody, GenericPackageInstantiation,
                         PackageRenamingDecl)

    @langkit_property()
    def is_library_package():
        """
        Property helper to determine if an entity is a library level package or
        not.
        """
        return Self.is_package & Self.is_library_item

    @langkit_property()
    def initial_env():
        """
        Provide a lexical environment to use in EnvSpec's initial_env.
        """
        return Self.parent.then(lambda p: p.children_env,
                                default_val=Self.children_env)


def child_unit(name_expr, scope_expr):
    """
    This macro will add the properties and the env specification necessary
    to make a node implement the specification of a library child unit in
    Ada, so that you can declare new childs to an unit outside of its own
    scope.

    :param AbstractExpression name_expr: The expression that will retrieve
        the name symbol for the decorated node.

    :param AbstractExpression scope_expr: The expression that will retrieve the
        scope node for the decorated node. If the scope node is not found, it
        should return EmptyEnv: in this case, the actual scope will become the
        root environment.

    :rtype: EnvSpec
    """

    return EnvSpec(
        initial_env=(
            env.bind(Self.initial_env, Let(
                lambda scope=scope_expr: If(scope == EmptyEnv, env, scope)
            ))
        ),
        add_env=True,
        add_to_env=add_to_env_kv(name_expr, Self),
        ref_envs=[ref_used_packages(), ref_generic_formals(), ref_std()],
        env_hook_arg=Self,
    )


@abstract
class BasicDecl(AdaNode):

    defining_names = AbstractProperty(
        type=T.Name.array, public=True, doc="""
        Get all the names of this basic declaration.
        """
    )

    defining_name = Property(
        Self.defining_names.at(0).as_entity, public=True, doc="""
        Get the name of this declaration. If this declaration has several
        names, it will return the first one.
        """
    )

    defining_env = Property(
        EmptyEnv,
        dynamic_vars=[origin],
        doc="""
        Return a lexical environment that contains entities that are accessible
        as suffixes when Self is a prefix.
        """
    )

    array_ndims = Property(
        Literal(0),
        doc="""
        If this designates an entity with an array-like interface, return its
        number of dimensions. Return 0 otherwise.
        """
    )

    is_array = Property(Self.array_ndims > 0)

    @langkit_property(return_type=T.BaseTypeDecl.entity,
                      dynamic_vars=[origin])
    def expr_type():
        """
        Return the type declaration corresponding to this basic declaration
        has when it is used in an expression context. For example, for this
        basic declaration::

            type Int is range 0 .. 100;

            A : Int := 12;

        the declaration of the Int type will be returned. For this
        declaration::

            type F is delta 0.01 digits 10;

            function B return F;

        expr_type will return the declaration of the type F.
        """
        return Self.type_expression._.designated_type

    type_expression = Property(
        No(T.TypeExpr).as_entity,
        type=T.TypeExpr.entity,
        doc="""
        Return the type expression for this BasicDecl if applicable, a null
        otherwise.
        """
    )

    @langkit_property(return_type=T.BaseTypeDecl.entity,
                      dynamic_vars=[origin])
    def canonical_expr_type():
        """
        Same as expr_type, but will instead return the canonical type
        declaration.
        """
        return Self.expr_type._.canonical_type

    @langkit_property(return_type=T.SubpSpec.entity)
    def subp_spec_or_null():
        """
        If node is a Subp, returns the specification of this subprogram.
        TODO: Enhance when we have interfaces.
        """
        return Self.match(
            lambda subp=BasicSubpDecl: subp.subp_decl_spec,
            lambda subp=SubpBody:      subp.subp_spec.as_entity,
            lambda _:                  No(SubpSpec.entity),
        )

    @langkit_property(return_type=EquationType, dynamic_vars=[origin])
    def constrain_prefix(prefix=T.Expr):
        """
        This method is used when self is a candidate suffix in a dotted
        expression, to express the potential constraint that the suffix could
        express on the prefix.

        For example, given this code::

            1 type P is record
            2     A, B : Integer;
            3 end record;
            4
            5 P_Inst : P;
            7
            8 P_Inst.A;
              ^^^^^^^^

        A references the A ComponentDecl at line 2, and the constraint that we
        want to express on the prefix (P_Inst), is that it needs to be of type
        P.
        """
        # Default implementation returns logic true => does not add any
        # constraint to the xref equation.
        ignore(prefix)
        return LogicTrue()

    declarative_scope = Property(
        Self.parents.find(
            lambda p: p.is_a(T.DeclarativePart)
        ).cast(T.DeclarativePart),
        doc="Return the scope of definition of this basic declaration.",
        ignore_warn_on_node=True
    )

    relative_name = Property(Self.defining_name.relative_name)

    @langkit_property()
    def body_part_entity():
        """
        Return the body corresponding to this node if applicable.
        """
        ignore(Var(Self.body_unit))
        return Self.children_env.get('__body', recursive=False).at(0)


@abstract
class Body(BasicDecl):
    pass


@abstract
class BodyStub(Body):
    pass


class DiscriminantSpec(BasicDecl):
    ids = Field(type=T.Identifier.list)
    type_expr = Field(type=T.TypeExpr)
    default_expr = Field(type=T.Expr)

    env_spec = EnvSpec(
        add_to_env=add_to_env(env_mappings(Self.ids, Self))
    )

    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))


@abstract
class DiscriminantPart(AdaNode):
    pass


class KnownDiscriminantPart(DiscriminantPart):
    discr_specs = Field(type=T.DiscriminantSpec.list)


class UnknownDiscriminantPart(DiscriminantPart):
    pass


@abstract
class TypeDef(AdaNode):
    array_ndims = Property(
        Literal(0),
        doc="""
        If this designates an array type, return its number of dimensions.
        Return 0 otherwise.
        """
    )

    is_real_type = Property(False, doc="Whether type is a real type or not.")

    @langkit_property(dynamic_vars=[origin])
    def is_int_type():
        """Whether type is an integer type or not."""
        return False

    is_access_type = Property(False,
                              doc="Whether type is an access type or not.")
    is_char_type = Property(False)

    @langkit_property(dynamic_vars=[origin])
    def accessed_type():
        return No(BaseTypeDecl.entity)

    is_tagged_type = Property(False, doc="Whether type is tagged or not")
    base_type = Property(
        No(T.BaseTypeDecl.entity), doc="""
        Return the base type entity for this derived type definition.
        """
    )

    @langkit_property(dynamic_vars=[origin])
    def defining_env():
        return EmptyEnv


class Variant(AdaNode):
    choice_list = Field(type=T.AdaNode.list)
    components = Field(type=T.ComponentList)


class VariantPart(AdaNode):
    discr_name = Field(type=T.Identifier)
    variant = Field(type=T.Variant.list)


@abstract
class BaseFormalParamDecl(BasicDecl):
    """
    Base class for formal parameter declarations. This is used both for records
    components and for subprogram parameters.
    """
    identifiers = AbstractProperty(type=T.BaseId.array)
    is_mandatory = Property(False)

    type = Property(
        origin.bind(Self, Self.type_expression.designated_type.canonical_type)
    )


class ComponentDecl(BaseFormalParamDecl):
    ids = Field(type=T.Identifier.list)
    component_def = Field(type=T.ComponentDef)
    default_expr = Field(type=T.Expr)
    aspects = Field(type=T.AspectSpec)

    env_spec = EnvSpec(
        add_to_env=add_to_env(env_mappings(Self.ids, Self)),
    )

    identifiers = Property(Self.ids.map(lambda e: e.cast(BaseId)))
    defining_env = Property(
        Self.component_def.type_expr.defining_env,
        doc="See BasicDecl.defining_env"
    )

    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))
    array_ndims = Property(Self.component_def.type_expr.array_ndims)

    type_expression = Property(Self.component_def.type_expr.as_entity)

    @langkit_property(return_type=EquationType)
    def constrain_prefix(prefix=T.Expr):
        # Simple type equivalence
        return Bind(prefix.type_var, Self.container_type,
                    eq_prop=BaseTypeDecl.matching_prefix_type)

    @langkit_property(return_type=T.BaseTypeDecl.entity)
    def container_type():
        """
        Return the defining container type for this component declaration.
        """
        return Self.parents.find(
            lambda p: p.is_a(BaseTypeDecl)
        ).cast(BaseTypeDecl).as_entity


@abstract
class BaseFormalParamHolder(AdaNode):
    """
    Base class for lists of formal parameters. This is used both for subprogram
    specifications and for records, so that we can share the matching and
    unpacking logic.
    """

    abstract_formal_params = AbstractProperty(
        type=BaseFormalParamDecl.entity.array,
        doc="Return the list of abstract formal parameters for this holder."
    )

    unpacked_formal_params = Property(
        Self.abstract_formal_params.mapcat(
            lambda spec: spec.identifiers.map(lambda id: (
                New(SingleFormal, name=id, spec=spec)
            ))
        ),
        doc='Couples (identifier, param spec) for all parameters'
    )

    @langkit_property(return_type=T.ParamMatch.array,
                      dynamic_vars=[env])
    def match_param_list(params=T.AssocList, is_dottable_subp=BoolType):
        """
        For each ParamAssoc in a AssocList, return whether we could find a
        matching formal in Self, and whether this formal is optional (i.e. has
        a default value).
        """
        def matches(formal, actual):
            return New(ParamMatch,
                       has_matched=True,
                       formal=formal,
                       actual=actual)

        unpacked_formals = Var(Self.unpacked_formal_params)

        return params.unpacked_params.map(lambda i, a: If(
            a.name.is_null,

            Let(lambda idx=If(is_dottable_subp, i + 1, i):
                # Positional parameter case: if this parameter has no
                # name association, make sure we have enough formals.
                unpacked_formals.at(idx).then(lambda sp: matches(sp, a))),

            # Named parameter case: make sure the designator is
            # actualy a name and that there is a corresponding
            # formal.
            a.name.then(lambda id: (
                unpacked_formals.find(lambda p: p.name.matches(id)).then(
                    lambda sp: matches(sp, a)
                )
            ))
        ))


class ComponentList(BaseFormalParamHolder):
    components = Field(type=T.AdaNode.list)
    variant_part = Field(type=T.VariantPart)

    type_def = Property(Self.parent.parent.cast(T.TypeDef).as_entity)

    parent_component_list = Property(
        Self.type_def.cast(T.DerivedTypeDef)._.base_type.record_def.comps
    )

    @langkit_property()
    def abstract_formal_params():
        # TODO: Incomplete definition. We need to handle variant parts.
        self_comps = Var(Self.components.keep(BaseFormalParamDecl).map(
            lambda e: e.as_entity
        ))

        return Self.parent_component_list.then(
            lambda pcl: pcl.abstract_formal_params.concat(self_comps),
            default_val=self_comps
        )


@abstract
class BaseRecordDef(AdaNode):
    components = Field(type=T.ComponentList)

    # TODO: Kludge, to remove when Q619-018 is implemented
    comps = Property(Self.components.as_entity)


class RecordDef(BaseRecordDef):
    pass


class NullRecordDef(BaseRecordDef):
    pass


class Tagged(EnumNode):
    qualifier = True


class Abstract(EnumNode):
    qualifier = True


class Limited(EnumNode):
    qualifier = True


class Private(EnumNode):
    qualifier = True


class Aliased(EnumNode):
    qualifier = True


class NotNull(EnumNode):
    qualifier = True


class Constant(EnumNode):
    qualifier = True


class All(EnumNode):
    qualifier = True


class Abort(EnumNode):
    qualifier = True


class Reverse(EnumNode):
    qualifier = True


class WithPrivate(EnumNode):
    qualifier = True


class Until(EnumNode):
    qualifier = True


class Synchronized(EnumNode):
    qualifier = True


class Protected(EnumNode):
    qualifier = True


class RecordTypeDef(TypeDef):
    has_abstract = Field(type=Abstract)
    has_tagged = Field(type=Tagged)
    has_limited = Field(type=Limited)
    record_def = Field(type=T.BaseRecordDef)

    defining_env = Property(
        # We don't want to be able to access env elements in parents,
        # so we orphan the env.
        Self.children_env.env_orphan,
        type=LexicalEnvType
    )

    is_tagged_type = Property(Self.has_tagged.as_bool)


@abstract
class RealTypeDef(TypeDef):
    is_real_type = Property(True)


@abstract
class BaseTypeDecl(BasicDecl):
    type_id = Field(type=T.Identifier)

    env_spec = EnvSpec(
        add_to_env=add_to_env_kv(Self.relative_name, Self)
    )

    defining_names = Property(Self.type_id.cast(T.Name).singleton)

    is_task_type = Property(False, doc="Whether type is a task type")
    is_real_type = Property(False, doc="Whether type is a real type or not.")

    @langkit_property(dynamic_vars=[origin])
    def is_int_type():
        """Whether type is an integer type or not."""
        return False

    is_access_type = Property(False,
                              doc="Whether type is an access type or not")

    is_char_type = Property(False,
                            doc="Whether type is a character type or not")

    @langkit_property(dynamic_vars=[origin])
    def is_str_type():
        return Self.is_array & Self.comp_type._.is_char_type

    @langkit_property(dynamic_vars=[origin])
    def accessed_type():
        return No(T.BaseTypeDecl.entity)

    is_tagged_type = Property(False, doc="Whether type is tagged or not")
    base_type = Property(
        No(T.BaseTypeDecl.entity), doc="""
        Return the base type entity for this derived type declaration.
        """
    )
    array_def = Property(No(T.ArrayTypeDef.entity))
    record_def = Property(No(T.BaseRecordDef.entity))

    @langkit_property(dynamic_vars=[origin])
    def comp_type():
        """
        Return the component type of the type, if applicable. The component
        type is the type you'll get if you call an instance of the Self type.
        So it can either be:

            1. The component type for an array.
            2. The return type for an access to function.
        """
        return Self.array_def._.comp_type

    # A BaseTypeDecl in an expression context corresponds to a type conversion,
    # so its type is itself.
    expr_type = Property(Self.as_entity)

    @langkit_property(return_type=BoolType)
    def is_derived_type(other_type=T.BaseTypeDecl.entity):
        """
        Whether Self is derived from other_type.
        """
        return Or(
            Self.as_entity == other_type,
            (Not(Self.classwide_type.is_null)
             & (Self.classwide_type == other_type.classwide_type)),
            Self.base_type._.is_derived_type(other_type)
        )

    is_iterable_type = Property(
        # TODO: Only works with array types at the moment, need to implement
        # on:
        #
        #   * Spark iterable types (Iterable aspect).
        #   * Ada 2012 iterable types.
        Self.is_array,
        doc="""
        Whether Self is a type that is iterable in a for .. of loop
        """
    )

    @langkit_property(return_type=BoolType, dynamic_vars=[origin])
    def matching_prefix_type(container_type=T.BaseTypeDecl.entity):
        """
        Given a dotted expression A.B, where container_type is the container
        type for B, and Self is a potential type for A, returns whether Self is
        a valid type for A in the dotted expression.
        """
        cont_type = Var(container_type.canonical_type)
        return Or(
            # Derived type case
            Self.canonical_type.is_derived_type(cont_type),

            # Access to derived type case
            Self.canonical_type.accessed_type._.is_derived_type(cont_type),
        )

    @langkit_property(return_type=BoolType, dynamic_vars=[origin])
    def matching_access_type(expected_type=T.BaseTypeDecl.entity):
        """
        Whether self is a matching access type for expected_type.
        """
        actual_type = Var(Self.as_entity)
        return expected_type.match(
            lambda atd=T.AnonymousTypeDecl.entity:
            atd.access_def_matches(actual_type),
            lambda _: False
        )

    @langkit_property(return_type=BoolType, dynamic_vars=[origin])
    def matching_formal_type(formal_type=T.BaseTypeDecl.entity):
        actual_type = Var(Self.as_entity)
        return Or(
            And(
                formal_type.is_classwide,
                actual_type.is_derived_type(formal_type)
            ),
            And(
                actual_type.is_classwide,
                actual_type.is_derived_type(formal_type)
            ),
            actual_type == formal_type,
            actual_type.matching_access_type(formal_type)
        )

    @langkit_property(return_type=BoolType, dynamic_vars=[origin])
    def matching_assign_type(expected_type=T.BaseTypeDecl.entity):
        actual_type = Var(Self.as_entity)
        return Or(
            Self.matching_type(expected_type),
            And(
                expected_type.is_classwide,
                actual_type.is_derived_type(expected_type)
            )
        )

    @langkit_property(return_type=BoolType, dynamic_vars=[origin])
    def matching_type(expected_type=T.BaseTypeDecl.entity):
        actual_type = Var(Self.as_entity)
        return Or(
            actual_type == expected_type,
            actual_type.matching_access_type(expected_type)
        )

    @langkit_property(return_type=BoolType, dynamic_vars=[origin])
    def matching_allocator_type(allocated_type=T.BaseTypeDecl.entity):
        return And(
            Self.is_access_type,
            allocated_type.matching_type(Self.accessed_type)
        )

    @langkit_property(return_type=T.BaseTypeDecl.entity,
                      dynamic_vars=[origin])
    def canonical_type():
        """
        Return the canonical type declaration for this type declaration. For
        subtypes, it will return the base type declaration.
        """
        return Self.as_entity

    classwide_type_node = Property(If(
        Self.is_tagged_type,
        New(T.ClasswideTypeDecl, type_id=Self.type_id),
        No(T.ClasswideTypeDecl)
    ), memoized=True, ignore_warn_on_node=True)

    classwide_type = Property(Self.classwide_type_node.as_entity)

    is_classwide = Property(False)


@synthetic
class ClasswideTypeDecl(BaseTypeDecl):
    """
    Synthetic node (not parsed, generated from a property call). Refers to the
    classwide type for a given tagged type. The aim is that those be mostly
    equivalent to their non-classwide type, except for some resolution rules.
    """
    # We don't want to add the classwide type to the environment
    env_spec = EnvSpec(call_parents=False)

    typedecl = Property(Self.parent.cast(BaseTypeDecl).as_entity)

    is_classwide = Property(True)

    is_tagged_type = Property(True)
    base_type = Property(Self.typedecl.base_type)
    record_def = Property(Self.typedecl.record_def)
    classwide_type = Property(Self.as_entity)
    is_iterable_type = Property(Self.typedecl.is_iterable_type)
    defining_env = Property(Self.typedecl.defining_env)


class TypeDecl(BaseTypeDecl):
    discriminants = Field(type=T.DiscriminantPart)
    type_def = Field(type=T.TypeDef)
    aspects = Field(type=T.AspectSpec)

    array_ndims = Property(Self.type_def.array_ndims)

    is_real_type = Property(Self.type_def.is_real_type)
    is_int_type = Property(Self.type_def.is_int_type)
    is_access_type = Property(Self.type_def.is_access_type)
    accessed_type = Property(Self.type_def.as_entity.accessed_type)
    is_tagged_type = Property(Self.type_def.is_tagged_type)
    base_type = Property(Self.type_def.base_type)
    is_char_type = Property(Self.type_def.is_char_type)

    array_def = Property(Self.type_def.cast(T.ArrayTypeDef).as_entity)

    defining_env = Property(
        # Evaluating in type env, because the defining environment of a type
        # is always its own.
        env.bind(Self.children_env, Self.type_def.defining_env)
    )

    env_spec = EnvSpec(add_env=True)

    record_def = Property(
        Self.type_def.match(
            lambda r=T.RecordTypeDef: r.record_def,
            lambda d=T.DerivedTypeDef: d.record_extension,
            lambda _: No(T.BaseRecordDef)
        ).as_entity
    )

    xref_entry_point = Property(True)

    @langkit_property(return_type=EquationType)
    def xref_equation():
        # TODO: Handle discriminants
        return Self.type_def.xref_equation


class AnonymousTypeDecl(TypeDecl):

    @langkit_property(return_type=BoolType, dynamic_vars=[origin])
    def access_def_matches(other=BaseTypeDecl.entity):
        """
        Returns whether:
        1. Self and other are both access types.
        2. Their access def matches structurally.
        """

        # If the anonymous type is an access type definition, then verify if
        #  the accessed type corresponds to other's accessed type.
        return Self.type_def.cast(AccessDef)._.accessed_type.matching_type(
            other.accessed_type
        )

    # We don't want to add anonymous type declarations to the lexical
    # environments, so we reset the env spec.
    env_spec = EnvSpec(call_parents=False)


class EnumTypeDecl(BaseTypeDecl):
    enum_literals = Field(type=T.EnumLiteralDecl.list)
    aspects = Field(type=T.AspectSpec)

    is_char_type = Property(Self.enum_literals.any(
        lambda lit: lit.enum_identifier.is_a(T.CharLiteral)
    ))


class FloatingPointDef(RealTypeDef):
    num_digits = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)


class OrdinaryFixedPointDef(RealTypeDef):
    delta = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)


class DecimalFixedPointDef(RealTypeDef):
    delta = Field(type=T.Expr)
    digits = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)


@abstract
class BaseAssoc(AdaNode):
    assoc_expr = AbstractProperty(
        type=T.Expr, public=True, ignore_warn_on_node=True
    )


@abstract
class Constraint(AdaNode):
    pass


class RangeConstraint(Constraint):
    range = Field(type=T.RangeSpec)


class DigitsConstraint(Constraint):
    digits = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)


class DeltaConstraint(Constraint):
    digits = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)


class IndexConstraint(Constraint):
    constraints = Field(type=T.AdaNode.list)


class DiscriminantConstraint(Constraint):
    constraints = Field(type=T.DiscriminantAssoc.list)


class DiscriminantAssoc(Constraint):
    ids = Field(type=T.Identifier.list)
    expr = Field(type=T.Expr)


class DerivedTypeDef(TypeDef):
    has_abstract = Field(type=Abstract)
    has_limited = Field(type=Limited)
    has_synchronized = Field(type=Synchronized)
    subtype_indication = Field(type=T.SubtypeIndication)
    interfaces = Field(type=T.Name.list)
    record_extension = Field(type=T.BaseRecordDef)
    has_with_private = Field(type=WithPrivate)

    array_ndims = Property(Self.base_type.array_ndims)

    base_type = Property(
        origin.bind(Self, Self.subtype_indication.designated_type)
    )

    is_real_type = Property(Self.base_type.is_real_type)
    is_int_type = Property(Self.base_type.is_int_type)
    is_access_type = Property(Self.base_type.is_access_type)
    is_char_type = Property(Self.base_type.is_char_type)
    accessed_type = Property(Self.base_type.accessed_type)
    is_tagged_type = Property(True)

    defining_env = Property(EnvGroup(
        Self.children_env.env_orphan,

        # Add environments from parent type defs
        Self.base_type.canonical_type.defining_env
    ))

    @langkit_property(return_type=EquationType)
    def xref_equation():
        return Self.subtype_indication.xref_equation


class PrivateTypeDef(TypeDef):
    has_abstract = Field(type=Abstract)
    has_tagged = Field(type=Tagged)
    has_limited = Field(type=Limited)

    is_tagged_type = Property(Self.has_tagged.as_bool)


class SignedIntTypeDef(TypeDef):
    range = Field(type=T.RangeSpec)
    is_int_type = Property(True)


class ModIntTypeDef(TypeDef):
    expr = Field(type=T.Expr)
    is_int_type = Property(True)


@abstract
class ArrayIndices(AdaNode):
    ndims = AbstractProperty(
        type=LongType,
        doc="""Number of dimensions described in this node."""
    )

    @langkit_property(return_type=EquationType, dynamic_vars=[origin])
    def constrain_index_expr(index_expr=T.Expr, dim=LongType):
        """
        Add a constraint on an expression passed as the index of an array
        access expression.

        For example::

            type A is array (Integer range 1 .. 10) of Integer;

            A_Inst : A;

            A_Inst (2);
            --      ^ Will add constraint on lit that it needs to be of type
            --      Integer.
        """
        ignore(index_expr, dim)
        return LogicTrue()


class UnconstrainedArrayIndices(ArrayIndices):
    types = Field(type=T.UnconstrainedArrayIndex.list)
    ndims = Property(Self.types.length)

    @langkit_property(return_type=EquationType)
    def constrain_index_expr(index_expr=T.Expr, dim=LongType):
        return Bind(index_expr.type_var,
                    Self.types.at(dim).designated_type.canonical_type)


class ConstrainedArrayIndices(ArrayIndices):
    list = Field(type=T.AdaNode.list)

    ndims = Property(Self.list.length)

    @langkit_property(return_type=EquationType)
    def constrain_index_expr(index_expr=T.Expr, dim=LongType):
        return Self.list.at(dim).match(
            lambda n=T.SubtypeIndication:
            Bind(index_expr.type_var, n.designated_type.canonical_type),

            # TODO: We need to parse Standard to express the fact that when
            # we've got an anonymous range in the array index definition,
            # the index needs to be of type Standard.Integer.
            lambda _: LogicTrue()
        )


class ComponentDef(AdaNode):
    has_aliased = Field(type=Aliased)
    type_expr = Field(type=T.TypeExpr)


class ArrayTypeDef(TypeDef):
    indices = Field(type=T.ArrayIndices)
    component_type = Field(type=T.ComponentDef)

    @langkit_property(dynamic_vars=[origin])
    def comp_type():
        """Returns the type stored as a component in the array."""
        return (Self.component_type.type_expr.as_entity
                .designated_type.canonical_type)

    array_ndims = Property(Self.indices.ndims)


class InterfaceKind(EnumNode):
    alternatives = ["limited", "task", "protected", "synchronized"]


class InterfaceTypeDef(TypeDef):
    interface_kind = Field(type=InterfaceKind)
    interfaces = Field(type=T.Name.list)

    is_tagged_type = Property(True)


class SubtypeDecl(BaseTypeDecl):
    subtype = Field(type=T.SubtypeIndication)
    aspects = Field(type=T.AspectSpec)

    array_ndims = Property(Self.subtype.array_ndims)
    defining_env = Property(Self.subtype.defining_env)

    canonical_type = Property(Self.subtype.designated_type.canonical_type)

    accessed_type = Property(Self.canonical_type.accessed_type)

    is_int_type = Property(Self.canonical_type.is_int_type)


class TaskDef(AdaNode):
    interfaces = Field(type=T.Name.list)
    public_part = Field(type=T.PublicPart)
    private_part = Field(type=T.PrivatePart)
    end_id = Field(type=T.Identifier)


class ProtectedDef(AdaNode):
    public_part = Field(type=T.PublicPart)
    private_part = Field(type=T.PrivatePart)
    end_id = Field(type=T.Identifier)


class TaskTypeDecl(BaseTypeDecl):
    discrs = Field(type=T.DiscriminantPart)
    aspects = Field(type=T.AspectSpec)
    definition = Field(type=T.TaskDef)
    is_task_type = Property(True)

    defining_names = Property(Self.type_id.cast(T.Name).singleton)

    env_spec = EnvSpec(
        add_to_env=add_to_env_kv(Self.type_id.sym, Self),
        add_env=True,
    )


class SingleTaskTypeDecl(TaskTypeDecl):
    pass
    env_spec = EnvSpec(
        # In this case, we don't want to add this type to the env, because it's
        # the single task that contains this type decl that will be added to
        # the env. So we don't call the inherited env spec.
        call_parents=False,
        add_env=True
    )


class ProtectedTypeDecl(BasicDecl):
    protected_type_name = Field(type=T.Identifier)
    discrs = Field(type=T.DiscriminantPart)
    aspects = Field(type=T.AspectSpec)
    interfaces = Field(type=T.Name.list)
    definition = Field(type=T.ProtectedDef)

    defining_names = Property(Self.protected_type_name.cast(T.Name).singleton)


@abstract
class AccessDef(TypeDef):
    has_not_null = Field(type=NotNull)

    is_access_type = Property(True)
    defining_env = Property(Self.accessed_type.defining_env)


class AccessToSubpDef(AccessDef):
    has_protected = Field(type=Protected, repr=False)
    subp_spec = Field(type=T.SubpSpec)


class TypeAccessDef(AccessDef):
    has_all = Field(type=All)
    has_constant = Field(type=Constant)
    subtype_indication = Field(type=T.SubtypeIndication)
    constraint = Field(type=T.Constraint)

    accessed_type = Property(Self.subtype_indication.designated_type)


class FormalDiscreteTypeDef(TypeDef):
    pass


class NullComponentDecl(AdaNode):
    pass


class WithClause(AdaNode):
    has_limited = Field(type=Limited)
    has_private = Field(type=Private)
    packages = Field(type=T.Name.list)

    env_spec = EnvSpec(env_hook_arg=Self)


@abstract
class UseClause(AdaNode):
    pass


class UsePackageClause(UseClause):
    packages = Field(type=T.Name.list)

    env_spec = EnvSpec(
        ref_envs=RefEnvs(
            T.Expr.designated_env_wrapper,

            # We don't want to process use clauses that appear in the top-level
            # scope here, as they apply to the library item's environment,
            # which is not processed at this point yet. See CompilationUnit's
            # ref_env_nodes.
            If(Self.parent.parent.is_a(T.CompilationUnit),
               EmptyArray(AdaNode),
               Self.packages.map(lambda n: n.cast(AdaNode)))
        )
    )


class UseTypeClause(UseClause):
    has_all = Field(type=All)
    types = Field(type=T.Name.list)


@abstract
class TypeExpr(AdaNode):
    """
    A type expression is an abstract node that embodies the concept of a
    reference to a type.

    Since Ada has both subtype_indications and anonymous (inline) type
    declarations, a type expression contains one or the other.
    """

    array_ndims = Property(origin.bind(Self, Self.designated_type.array_ndims))

    @langkit_property(dynamic_vars=[origin])
    def accessed_type():
        return Self.designated_type.accessed_type

    @langkit_property(dynamic_vars=[origin])
    def defining_env():
        return Self.designated_type.defining_env

    designated_type = AbstractProperty(
        type=BaseTypeDecl.entity, runtime_check=True,
        dynamic_vars=[origin],
        doc="""
        Return the type designated by this type expression.
        """
    )

    @langkit_property(return_type=BaseTypeDecl.entity, dynamic_vars=[origin])
    def element_type():
        """
        If self is an anonymous access, return the accessed type. Otherwise,
        return the designated type.
        """
        d = Self.designated_type
        return If(d.is_null, Self.accessed_type, d)


class AnonymousType(TypeExpr):
    """
    Container for inline anonymous array and access types declarations.
    """
    type_decl = Field(type=T.AnonymousTypeDecl)

    designated_type = Property(Self.type_decl.as_entity)


class SubtypeIndication(TypeExpr):
    has_not_null = Field(type=NotNull)
    name = Field(type=T.Name)
    constraint = Field(type=T.Constraint)

    # The name for this type has to be evaluated in the context of the
    # SubtypeIndication node itself: we don't want to use whatever lexical
    # environment the caller is using. However we need to inherit the
    # visibility (origin node) of the caller.
    designated_type = Property(env.bind(Self.node_env,
                                        Self.name.designated_type_impl))

    @langkit_property()
    def xref_equation():
        # Called by allocator.xref_equation, since the suffix can be either a
        # qual expr or a subtype indication.
        return Bind(Self.name.ref_var, Self.designated_type)


class ConstrainedSubtypeIndication(SubtypeIndication):
    pass


class DiscreteSubtypeIndication(SubtypeIndication):
    pass


class Mode(EnumNode):
    alternatives = ["in", "out", "in_out", "default"]


class ParamSpec(BaseFormalParamDecl):
    ids = Field(type=T.Identifier.list)
    has_aliased = Field(type=Aliased)
    mode = Field(type=Mode)
    type_expr = Field(type=T.TypeExpr)
    default = Field(type=T.Expr)

    identifiers = Property(Self.ids.map(lambda e: e.cast(BaseId)))
    is_mandatory = Property(Self.default.is_null)
    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))

    env_spec = EnvSpec(
        add_to_env=add_to_env(env_mappings(Self.ids, Self))
    )

    type_expression = Property(Self.type_expr.as_entity)


class AspectSpec(AdaNode):
    aspect_assocs = Field(type=T.AspectAssoc.list)


class Overriding(EnumNode):
    alternatives = ["overriding", "not_overriding", "unspecified"]


@abstract
class BasicSubpDecl(BasicDecl):
    defining_names = Property(Self.subp_decl_spec.name.singleton)
    defining_env = Property(Self.subp_decl_spec.defining_env)

    type_expression = Property(
        Self.subp_decl_spec.returns, doc="""
        The expr type of a subprogram declaration is the return type of the
        subprogram if the subprogram is a function.
        """
    )

    subp_decl_spec = AbstractProperty(type=T.SubpSpec.entity)

    @langkit_property(public=True, ignore_warn_on_node=True)
    def body_part():
        """
        Return the SubpBody corresponding to this node.
        """
        return Self.body_part_entity.cast(SubpBody).el

    env_spec = EnvSpec(
        initial_env=env.bind(Self.initial_env,
                             Self.subp_decl_spec.name.parent_scope),
        add_to_env=[
            # First regular add to env action, adding the subprogram to it's
            # scope.
            add_to_env_kv(Self.relative_name, Self),

            # Second custom action, adding to the type's environment if the
            # type is tagged and self is a primitive of it.
            add_to_env(
                # TODO: We can refactor this to not use an array, thanks to
                # mappings.
                Self.subp_decl_spec.dottable_subp.map(lambda dp: New(
                    T.env_assoc,
                    key=Self.relative_name, val=dp
                )),
                dest_env=Let(
                    lambda spec=Self.subp_decl_spec:
                    origin.bind(spec.el,
                                spec.potential_dottable_type._.children_env)
                ),
                # We pass custom metadata, marking the entity as a dottable
                # subprogram.
                metadata=New(Metadata, dottable_subp=True,
                             implicit_deref=False),

                # potential_dottable_type will need the SubtypeIndication
                # instance to have an associated environment, so we need to do
                # this after environments have been populated for the children.
                is_post=True
            )
        ],
        add_env=True,
        ref_envs=[ref_used_packages(), ref_std()],
        # Call the env hook so that library-level subprograms have their
        # parent unit (if any) environment.
        env_hook_arg=Self,
    )


@abstract
class ClassicSubpDecl(BasicSubpDecl):
    """
    This is an intermediate abstract class for subprogram declarations with a
    common structure: overriding indicator, subp_spec, aspects, <other fields>.
    """
    overriding = Field(type=Overriding)
    subp_spec = Field(type=T.SubpSpec)

    subp_decl_spec = Property(Self.subp_spec.as_entity)


class SubpDecl(ClassicSubpDecl):
    aspects = Field(type=T.AspectSpec)


class NullSubpDecl(ClassicSubpDecl):
    aspects = Field(type=T.AspectSpec)


class AbstractSubpDecl(ClassicSubpDecl):
    aspects = Field(type=T.AspectSpec)


class ExprFunction(ClassicSubpDecl):
    expr = Field(type=T.Expr)
    aspects = Field(type=T.AspectSpec)


class SubpRenamingDecl(ClassicSubpDecl):
    renames = Field(type=T.RenamingClause)
    aspects = Field(type=T.AspectSpec)


class Pragma(AdaNode):
    id = Field(type=T.Identifier)
    args = Field(type=T.BaseAssoc.list)


class PragmaArgumentAssoc(BaseAssoc):
    id = Field(type=T.Identifier)
    expr = Field(type=T.Expr)
    assoc_expr = Property(Self.expr)


@abstract
class AspectClause(AdaNode):
    pass


class EnumRepClause(AspectClause):
    type_name = Field(type=T.Name)
    aggregate = Field(type=T.BaseAggregate)


class AttributeDefClause(AspectClause):
    attribute_expr = Field(type=T.Expr)
    expr = Field(type=T.Expr)


class ComponentClause(AdaNode):
    id = Field(type=T.Identifier)
    position = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)


class RecordRepClause(AspectClause):
    component_name = Field(type=T.Name)
    at_expr = Field(type=T.Expr)
    components = Field(type=T.ComponentClause.list)


class AtClause(AspectClause):
    name = Field(type=T.BaseId)
    expr = Field(type=T.Expr)


class SingleTaskDecl(BasicDecl):
    task_type = Field(type=T.SingleTaskTypeDecl)
    defining_names = Property(Self.task_type.type_id.cast(T.Name).singleton)

    env_spec = EnvSpec(
        add_to_env=add_to_env_kv(Self.task_type.type_id.sym, Self)
    )

    expr_type = Property(Self.task_type.as_entity)


class SingleProtectedDecl(BasicDecl):
    protected_name = Field(type=T.Identifier)
    aspects = Field(type=T.AspectSpec)
    interfaces = Field(type=T.Name.list)
    definition = Field(type=T.ProtectedDef)

    defining_names = Property(Self.protected_name.cast(T.Name).singleton)


class AspectAssoc(AdaNode):
    id = Field(type=T.Expr)
    expr = Field(type=T.Expr)


class NumberDecl(BasicDecl):
    ids = Field(type=T.Identifier.list)
    expr = Field(type=T.Expr)

    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))


class ObjectDecl(BasicDecl):
    ids = Field(type=T.Identifier.list)
    has_aliased = Field(type=Aliased)
    has_constant = Field(type=Constant)
    inout = Field(type=Mode)
    type_expr = Field(type=T.TypeExpr)
    default_expr = Field(type=T.Expr)
    renaming_clause = Field(type=T.RenamingClause)
    aspects = Field(type=T.AspectSpec)

    env_spec = EnvSpec(
        add_to_env=add_to_env(env_mappings(Self.ids, Self))
    )

    array_ndims = Property(Self.type_expr.array_ndims)
    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))
    defining_env = Property(Self.type_expr.defining_env)
    type_expression = Property(Self.type_expr.as_entity)

    @langkit_property()
    def xref_equation():
        return Self.default_expr.then(
            lambda de:
            de.xref_equation
            & Bind(Self.default_expr.type_var,
                   Self.canonical_expr_type,
                   eq_prop=BaseTypeDecl.matching_assign_type),
            default_val=LogicTrue()
        )

    xref_entry_point = Property(True)


class ExtendedReturnStmtObjectDecl(ObjectDecl):
    pass


class DeclarativePart(AdaNode):
    decls = Field(type=T.AdaNode.list)


class PrivatePart(DeclarativePart):
    env_spec = EnvSpec(
        add_env=True,
        add_to_env=add_to_env_kv('__privatepart', Self)
    )


class PublicPart(DeclarativePart):
    pass


@abstract
class BasePackageDecl(BasicDecl):
    """
    Package declarations. Concrete instances of this class
    will be created in generic package declarations. Other non-generic
    package declarations will be instances of PackageDecl.

    The behavior is the same, the only difference is that BasePackageDecl
    and PackageDecl have different behavior regarding lexical environments.
    In the case of generic package declarations, we use BasePackageDecl
    which has no env_spec, and the environment behavior is handled by the
    GenericPackageDecl instance.
    """
    package_name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)
    public_part = Field(type=T.PublicPart)
    private_part = Field(type=T.PrivatePart)
    end_id = Field(type=T.Name)

    defining_names = Property(Self.package_name.singleton)
    defining_env = Property(Self.children_env.env_orphan)

    @langkit_property(return_type=T.PackageBody, public=True,
                      ignore_warn_on_node=True)
    def body_part():
        """
        Return the PackageBody corresponding to this node.
        """
        return Self.body_part_entity.cast(T.PackageBody).el


class PackageDecl(BasePackageDecl):
    """
    Non-generic package declarations.
    """
    env_spec = child_unit(Self.relative_name, Self.package_name.parent_scope)


class ExceptionDecl(BasicDecl):
    """
    Exception declarations.
    """
    ids = Field(type=T.Identifier.list)
    renames = Field(type=T.RenamingClause)
    aspects = Field(type=T.AspectSpec)
    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))

    env_spec = EnvSpec(
        add_to_env=add_to_env(env_mappings(Self.ids, Self))
    )


@abstract
class GenericInstantiation(BasicDecl):
    """
    Instantiations of generics.
    """

    instantiation_env_holder = Field(type=T.EnvHolder)

    generic_entity_name = AbstractProperty(
        type=T.Name.entity, doc="""
        Return the name of the generic entity designated by this generic
        instantiation
        """
    )

    designated_generic_decl = Property(
        env.bind(Self.node_env, Self.generic_entity_name.env_elements.at(0))
        .cast_or_raise(T.GenericDecl),
        doc="""
        Return the formal package designated by the right hand part of this
        generic package instantiation.
        """
    )


class EnvHolder(AdaNode):
    """
    This type does not correspond to anything in the source. It is just here
    to hold a lexical environment.

    TODO: This should be do-able in a simpler fashion, by exposing a
    LexicalEnvType field that is automatically initialized.
    """
    env_spec = EnvSpec(add_env=True)


class GenericSubpInstantiation(GenericInstantiation):
    overriding = Field(type=Overriding)
    kind = Field(type=T.SubpKind)
    subp_name = Field(type=T.Name)
    generic_subp_name = Field(type=T.Name)
    subp_params = Field(type=T.AssocList)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.subp_name.singleton)

    generic_entity_name = Property(Self.generic_subp_name.as_entity)


class GenericPackageInstantiation(GenericInstantiation):
    name = Field(type=T.Name)
    generic_pkg_name = Field(type=T.Name)
    params = Field(type=T.AssocList)
    aspects = Field(type=T.AspectSpec)

    generic_entity_name = Property(Self.generic_pkg_name.as_entity)

    @langkit_property(return_type=LexicalEnvType)
    def defining_env():
        p = Var(Self.designated_generic_decl)
        formal_env = Var(p.children_env)

        return p.decl.children_env.rebind_env(
            formal_env, Self.instantiation_env_holder.children_env
        )

    defining_names = Property(Self.name.singleton)

    env_spec = EnvSpec(
        add_to_env=[
            add_to_env_kv(Self.relative_name, Self),
            add_to_env(
                env.bind(
                    Self.initial_env,
                    Self.designated_generic_decl.formal_part.match_param_list(
                        Self.params, False
                    ).map(lambda pm: New(
                        T.env_assoc,
                        key=pm.formal.name.sym, val=pm.actual.assoc.expr
                    ))
                ),
                is_post=True,
                dest_env=Self.instantiation_env_holder.children_env,
                resolver=AdaNode.resolve_generic_actual,
            )
        ]
    )


class RenamingClause(AdaNode):
    """
    Renaming clause, used everywhere renamings are valid.
    """
    renamed_object = Field(type=T.Expr)


class PackageRenamingDecl(BasicDecl):
    name = Field(type=T.Name)
    renames = Field(type=RenamingClause)
    aspects = Field(type=T.AspectSpec)

    env_spec = child_unit(Self.relative_name, Self.name.parent_scope)

    defining_names = Property(Self.name.singleton)
    defining_env = Property(env.bind(
        Self.node_env,
        Self.renames.renamed_object.matching_nodes_impl.at(0).cast(BasicDecl)
        .defining_env
    ))


@abstract
class GenericRenamingDecl(BasicDecl):
    """
    Base node for all generic renaming declarations.
    """
    pass


class GenericPackageRenamingDecl(GenericRenamingDecl):
    name = Field(type=T.Name)
    renames = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class SubpKind(EnumNode):
    alternatives = ["procedure", "function"]


class GenericSubpRenamingDecl(GenericRenamingDecl):
    kind = Field(type=T.SubpKind)
    name = Field(type=T.Name)
    renames = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


@abstract
class FormalSubpDecl(ClassicSubpDecl):
    """
    Formal subprogram declarations, in generic declarations formal parts.
    """
    default_value = Field(type=T.Expr)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.subp_spec.name.singleton)


class ConcreteFormalSubpDecl(FormalSubpDecl):
    pass


class AbstractFormalSubpDecl(FormalSubpDecl):
    pass


class GenericFormalPart(BaseFormalParamHolder):
    decls = Field(type=T.AdaNode.list)

    abstract_formal_params = Property(
        Self.decls.keep(BaseFormalParamDecl).map(lambda e: e.as_entity)
    )


@abstract
class GenericFormal(BaseFormalParamDecl):
    decl = Field(T.BasicDecl)
    identifiers = Property(
        Self.decl.defining_names.map(lambda p: p.cast_or_raise(T.BaseId))
    )
    defining_names = Property(Self.decl.defining_names)


class GenericFormalObjDecl(GenericFormal):
    pass


class GenericFormalTypeDecl(GenericFormal):
    pass


class GenericFormalSubpDecl(GenericFormal):
    pass


class GenericFormalPackage(GenericFormal):
    pass


class GenericSubpInternal(BasicSubpDecl):
    subp_spec = Field(type=T.SubpSpec)
    aspects = Field(type=T.AspectSpec)

    subp_decl_spec = Property(Self.subp_spec.as_entity)
    env_spec = EnvSpec(add_env=True)


@abstract
class GenericDecl(BasicDecl):
    formal_part = Field(type=T.GenericFormalPart)
    decl = AbstractProperty(type=T.BasicDecl.entity)


class GenericSubpDecl(GenericDecl):
    env_spec = child_unit(Self.relative_name,
                          Self.subp_decl.subp_spec.name.parent_scope)

    subp_decl = Field(type=T.GenericSubpInternal)

    defining_names = Property(Self.subp_decl.subp_spec.name.singleton)

    @langkit_property(public=True, ignore_warn_on_node=True)
    def body_part():
        """
        Return the SubpBody corresponding to this node.
        """
        return Self.body_part_entity.cast(SubpBody).el

    env_spec = EnvSpec(
        initial_env=env.bind(Self.initial_env,
                             Self.subp_decl.subp_spec.name.parent_scope),
        add_to_env=[add_to_env_kv(Self.relative_name, Self)],
        add_env=True,
        ref_envs=[ref_used_packages(), ref_std()],
        # Call the env hook so that library-level subprograms have their
        # parent unit (if any) environment.
        env_hook_arg=Self,
    )

    decl = Property(Self.subp_decl.as_entity)


class GenericPackageInternal(BasePackageDecl):
    """
    This class denotes the internal package contained by a GenericPackageDecl.
    """
    # Implementation note: This exists so that we can insert an environment to
    # distinguish between formal parameters and the package's contents.

    env_spec = EnvSpec(add_env=True)


class GenericPackageDecl(GenericDecl):
    env_spec = child_unit(Self.relative_name,
                          Self.package_decl.package_name.parent_scope)

    package_decl = Field(type=GenericPackageInternal)

    defining_names = Property(Self.package_decl.package_name.singleton)

    @langkit_property(public=True, ignore_warn_on_node=True)
    def body_part():
        """
        Return the PackageBody corresponding to this node, or null if there is
        none.
        """
        return Self.package_decl.body_part

    decl = Property(Self.package_decl.as_entity)


@abstract
class Expr(AdaNode):

    type_var = UserField(LogicVarType, public=False)
    type_val = Property(Self.type_var.get_value)

    @langkit_property(kind=AbstractKind.abstract_runtime_check,
                      return_type=LexicalEnvType, dynamic_vars=[env, origin])
    def designated_env():
        """
        Returns the lexical environment designated by this name.

        If this name involves overloading, this will return a combination of
        the various candidate lexical environments.
        """
        pass

    @langkit_property()
    def designated_env_wrapper():
        return env.bind(Self.node_env, origin.bind(Self, Self.designated_env))

    parent_scope = AbstractProperty(
        type=LexicalEnvType, runtime_check=True,
        dynamic_vars=[env],
        doc="""
        Returns the lexical environment that is the scope in which the
        entity designated by this name is defined/used.
        """
    )

    relative_name = AbstractProperty(
        type=Symbol, runtime_check=True,
        doc="""
        Returns the relative name of this instance. For example,
        for a prefix A.B.C, this will return C.
        """
    )

    env_elements = Property(
        Self.env_elements_impl().filter(lambda e: (
            Not(e.is_library_item)
            | Self.has_with_visibility(e.el.unit)
        )),
        dynamic_vars=[env]
    )

    @langkit_property(return_type=AdaNode.entity.array,
                      kind=AbstractKind.abstract_runtime_check,
                      dynamic_vars=[env])
    def env_elements_impl():
        """
        Returns the list of annotated elements in the lexical environment
        that can statically be a match for expr before overloading analysis.
        """
        pass

    @langkit_property(dynamic_vars=[env])
    def matching_nodes_impl():
        return Self.env_elements.map(lambda e: e.el)

    @langkit_property(return_type=AdaNode.array, public=True)
    def matching_nodes():
        """
        Return the list of AST nodes that can be a match for this expression
        before overloading analysis.
        """
        return env.bind(Self.node_env, Self.matching_nodes_impl)


class ContractCaseAssoc(BaseAssoc):
    guard = Field(type=T.AdaNode)
    consequence = Field(type=T.Expr)

    assoc_expr = Property(Self.consequence)


class ContractCases(Expr):
    contract_cases = Field(ContractCaseAssoc.list)


class ParenExpr(Expr):
    expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return Self.expr.sub_equation & Bind(Self.expr.type_var, Self.type_var)


class Op(EnumNode):
    """
    Operation in a binary expression.

    Note that the ARM does not consider "double_dot" ("..") as a binary
    operator, but we process it this way here anyway to keep things simple.
    """
    alternatives = ["and", "or", "or_else", "and_then", "xor", "in",
                    "not_in", "abs", "not", "pow", "mult", "div", "mod",
                    "rem", "plus", "minus", "concat", "eq", "neq", "lt",
                    "lte", "gt", "gte", "double_dot"]

    subprograms = Property(
        lambda: Self.node_env.get(Self.match(
            lambda _=Op.alt_and: '"and"',
            lambda _=Op.alt_or: '"or"',
            lambda _=Op.alt_xor: '"xor"',
            lambda _=Op.alt_abs: '"abs"',
            lambda _=Op.alt_not: '"not"',
            lambda _=Op.alt_pow: '"**"',
            lambda _=Op.alt_mult: '"*"',
            lambda _=Op.alt_div: '"/"',
            lambda _=Op.alt_mod: '"mod"',
            lambda _=Op.alt_rem: '"rem"',
            lambda _=Op.alt_plus: '"+"',
            lambda _=Op.alt_minus: '"-"',
            lambda _=Op.alt_concat: '"&"',
            lambda _=Op.alt_eq: '"="',
            lambda _=Op.alt_neq: '"/="',
            lambda _=Op.alt_lt: '"<"',
            lambda _=Op.alt_lte: '"<="',
            lambda _=Op.alt_gt: '">"',
            lambda _=Op.alt_gte: '">="',
            lambda _: '<<>>',
        )).keep(T.BasicSubpDecl.entity),
        doc="""
        Return the subprograms corresponding to this operator accessible in the
        lexical environment.
        """
    )

    ref_var = UserField(type=LogicVarType, public=False)


class UnOp(Expr):
    op = Field(type=Op)
    expr = Field(type=T.Expr)


class BinOp(Expr):
    left = Field(type=T.Expr)
    op = Field(type=Op)
    right = Field(type=T.Expr)

    ref_val = Property(Self.op.ref_var.get_value)

    @langkit_property()
    def xref_equation():
        subps = Var(Self.op.subprograms)
        return (
            Self.left.sub_equation & Self.right.sub_equation
        ) & (subps.logic_any(lambda subp: Let(
            lambda ps=subp.subp_decl_spec.unpacked_formal_params:

            # The subprogram's first argument must match Self's left
            # operand.
            Bind(Self.left.type_var, ps.at(0).spec.type)

            # The subprogram's second argument must match Self's right
            # operand.
            & Bind(Self.right.type_var, ps.at(1).spec.type)

            # The subprogram's return type is the type of Self
            & Bind(Self.type_var, subp.subp_decl_spec.returns.designated_type)

            # The operator references the subprogram
            & Bind(Self.op.ref_var, subp)
        )) | Self.no_overload_equation())

    no_overload_equation = Property(
        Bind(Self.type_var, Self.left.type_var)
        & Bind(Self.type_var, Self.right.type_var),
        doc="""
        When no subprogram is found for this node's operator, use this property
        to construct the xref equation for this node.
        """
    )


class RelationOp(BinOp):
    no_overload_equation = Property(
        Bind(Self.left.type_var, Self.right.type_var)
        & Bind(Self.type_var, Self.bool_type)
    )


class MembershipExpr(Expr):
    """
    Represent a membership test (in/not in operators).

    Note that we don't consider them as binary operators since multiple
    expressions on the right hand side are allowed.
    """
    expr = Field(type=T.Expr)
    op = Field(type=Op)
    membership_exprs = Field(type=T.AdaNode.list)


@abstract
class BaseAggregate(Expr):
    ancestor_expr = Field(type=T.Expr)
    assocs = Field(type=T.AssocList)


class Aggregate(BaseAggregate):

    xref_stop_resolution = Property(True)

    @langkit_property()
    def xref_equation():
        td = Var(Self.type_val.cast(BaseTypeDecl.entity))

        atd = Var(td.array_def)
        return If(
            atd.is_null,

            # First case, aggregate for a record
            td.record_def.comps.match_param_list(
                Self.assocs, False
            ).logic_all(
                lambda pm:
                Bind(pm.actual.assoc.expr.type_var,
                     pm.formal.spec.type_expression.designated_type)
                & pm.actual.assoc.expr.sub_equation
                & If(pm.actual.name.is_null,
                     LogicTrue(),
                     Bind(pm.actual.name.ref_var, pm.formal.spec))
            ),

            # Second case, aggregate for an array
            Self.assocs.logic_all(
                lambda assoc:
                assoc.expr.sub_equation
                & Bind(assoc.expr.type_var, atd.comp_type)
            )
        )


class NullRecordAggregate(BaseAggregate):
    pass


@abstract
class Name(Expr):

    scope = Property(
        EmptyEnv, dynamic_vars=[env],
        doc="""
        Lexical environment this identifier represents. This is similar to
        designated_env although it handles only cases for child units and it is
        used only during the environment population pass so it does not return
        orphan environments.
        """
    )

    @langkit_property(kind=AbstractKind.abstract_runtime_check,
                      return_type=LogicVarType)
    def ref_var():
        """
        This property proxies the logic variable that points to the entity that
        this name refers to. For example, for a simple dotted name::

            A.B

        The dotted name's ref var is the one of the SingleTokNode B.
        """
        pass

    ref_val = Property(Self.ref_var.get_value)

    designated_type_impl = AbstractProperty(
        type=BaseTypeDecl.entity, runtime_check=True,
        dynamic_vars=[env, origin],
        doc="""
        Assuming this name designates a type, return this type.

        Since in Ada this can be resolved locally without any non-local
        analysis, this doesn't use logic equations.
        """
    )

    name_designated_type = Property(
        env.bind(Self.node_env, origin.bind(Self, Self.designated_type_impl)),
        doc="""
        Like SubtypeIndication.designated_type, but on names, since because of
        Ada's ambiguous grammar, some subtype indications will be parsed as
        names.
        """,
        public=True
    )

    @langkit_property(return_type=AnalysisUnitType, external=True,
                      uses_entity_info=False)
    def referenced_unit(kind=AnalysisUnitKind):
        """
        Return the analysis unit for the given "kind" corresponding to this
        Name. Return null if this is an illegal unit name.
        """
        pass

    @langkit_property(public=True)
    def matches(n=T.Name):
        """
        Return whether two names match each other.

        This compares the symbol for Identifier and StringLiteral nodes. We
        consider that there is no match for all other node kinds.
        """
        return Self.match(
            lambda id=Identifier:
                n.cast(Identifier).then(
                    lambda other_id: id.sym.equals(other_id.sym)
                ),
            lambda sl=StringLiteral:
                n.cast(StringLiteral).then(
                    lambda other_sl: sl.sym.equals(other_sl.sym)
                ),
            lambda _: False
        )


class CallExpr(Name):
    """
    Represent a syntactic call expression.

    At the semantic level, this can be either a subprogram call, an array
    subcomponent access expression, an array slice or a type conversion.
    """
    name = Field(type=T.Name)
    suffix = Field(type=T.AdaNode)

    ref_var = Property(Self.name.ref_var)

    @langkit_property()
    def designated_env():
        return Self.env_elements().map(lambda e: e.match(
            lambda subp=BasicSubpDecl.entity: subp.defining_env,
            lambda subp=SubpBody.entity:      subp.defining_env,
            lambda _:                           EmptyEnv,
        )).env_group

    @langkit_property()
    def env_elements_impl():
        return Self.name.env_elements_impl()

    # CallExpr can appear in type expressions: they are used to create implicit
    # subtypes for discriminated records or arrays.
    designated_type_impl = Property(Self.name.designated_type_impl)

    params = Property(Self.suffix.cast(T.AssocList), ignore_warn_on_node=True)

    @langkit_property(return_type=EquationType)
    def xref_equation():
        return If(
            Not(Self.name.designated_type_impl.is_null),

            # Type conversion case
            Self.type_conv_xref_equation,

            # General case. We'll call general_xref_equation on the innermost
            # call expression, to handle nested call expression cases.
            Self.innermost_callexpr.general_xref_equation
        )

    @langkit_property(return_type=EquationType, dynamic_vars=[env, origin])
    def type_conv_xref_equation():
        """
        Helper for xref_equation, handles construction of the equation in type
        conversion cases.
        """
        return And(
            Self.params.at(0).expr.sub_equation,
            Self.name.sub_equation,
            Bind(Self.type_var, Self.name.type_var),
            Bind(Self.ref_var, Self.name.ref_var)
        )

    @langkit_property(return_type=EquationType, dynamic_vars=[env, origin])
    def general_xref_equation():
        """
        Helper for xref_equation, handles construction of the equation in
        subprogram call cases.
        """
        # List of every applicable subprogram
        subps = Var(Self.env_elements)

        return (
            Self.name.sub_equation
            # TODO: For the moment we presume that a CallExpr in an expression
            # context necessarily has a AssocList as a suffix, but this is not
            # always true (for example, entry families calls). Handle the
            # remaining cases.
            & Self.params.logic_all(lambda pa: pa.expr.sub_equation)

            # For each potential subprogram match, we want to express the
            # following constraints:
            & subps.logic_any(lambda e: Let(
                lambda s=e.cast(BasicDecl.entity):

                # The called entity is the subprogram
                Bind(Self.name.ref_var, e)

                & If(
                    # Test if the entity is a parameterless subprogram call,
                    # or something else (a component/local variable/etc),
                    # that would make this callexpr an array access.
                    s.subp_spec_or_null.then(
                        lambda ss: ss.paramless(e.info.md),
                        default_val=True
                    ),

                    Self.equation_for_type(s.expr_type),

                    # The type of the expression is the expr_type of the
                    # subprogram.
                    Bind(Self.type_var, s.expr_type)

                    # For each parameter, the type of the expression matches
                    # the expected type for this subprogram.
                    & s.subp_spec_or_null.match_param_list(
                        Self.params, e.info.md.dottable_subp
                    ).logic_all(
                        lambda pm: (
                            # The type of each actual matches the type of the
                            # formal.
                            Bind(
                                pm.actual.assoc.expr.type_var,
                                pm.formal.spec.type_expression.designated_type,
                                eq_prop=BaseTypeDecl.matching_formal_type
                            )
                        ) & If(
                            # Bind actuals designators to parameters if there
                            # are designators.
                            pm.actual.name.is_null,
                            LogicTrue(),
                            Bind(pm.actual.name.ref_var, pm.formal.spec)
                        )
                    )
                )
                # For every callexpr between self and the furthest callexpr
                # that is an ancestor of Self via the name chain, we'll
                # construct the crossref equation.
                & Self.parent_nested_callexpr.then(
                    lambda pce: pce.parent_callexprs_equation(
                        s.expr_type.comp_type
                    ), default_val=LogicTrue()
                )
            ))

            # Bind the callexpr's ref_var to the id's ref var
            & Bind(Self.ref_var, Self.name.ref_var)
        )

    @langkit_property(return_type=EquationType, dynamic_vars=[env, origin])
    def equation_for_type(typ=T.BaseTypeDecl.entity):
        """
        Construct an equation verifying if Self is conformant to the type
        designator passed in parameter.
        """
        atd = Var(typ.array_def)

        return Let(lambda indices=atd.indices: Self.params.logic_all(
            lambda i, pa:
            pa.expr.sub_equation()
            & indices.constrain_index_expr(pa.expr, i)
        )) & Bind(Self.type_var, atd.comp_type)

    @langkit_property(return_type=BoolType)
    def check_type_internal(typ=T.BaseTypeDecl.entity):
        """
        Internal helper for check_type. Will call check_type_self on Self and
        all parent CallExprs.
        """
        return And(
            # TODO: For the moment this is specialized for arrays, but we need
            # to handle the case when the return value is an access to
            # subprogram.
            typ.array_ndims == Self.suffix.cast_or_raise(AssocList).length,
            Self.parent.cast(T.CallExpr).then(
                lambda ce: ce.check_type_internal(
                    origin.bind(Self, typ.comp_type)
                ), default_val=True
            )
        )

    @langkit_property(return_type=BoolType)
    def check_type(typ=T.BaseTypeDecl.entity):
        """
        Verifies that this callexpr is valid for the type designated by typ.
        """
        # Algorithm: We're:
        # 1. Taking the innermost call expression
        # 2. Recursing down call expression and component types up to self,
        # checking for each level that the call expression corresponds.
        return Self.innermost_callexpr.check_type_internal(typ)

    @langkit_property(return_type=T.CallExpr, ignore_warn_on_node=True)
    def innermost_callexpr():
        """
        Helper property. Will return the innermost call expression following
        the name chain. For, example, given::

            A (B) (C) (D)
            ^-----------^ Self
            ^-------^     Self.name
            ^---^         Self.name.name

        Self.innermost_callexpr will return the node corresponding to
        Self.name.name.
        """
        return Self.name.cast(T.CallExpr).then(
            lambda ce: ce.innermost_callexpr(), default_val=Self
        )

    @langkit_property(return_type=T.CallExpr, ignore_warn_on_node=True)
    def parent_nested_callexpr():
        """
        Will return the parent callexpr iff Self is the name of the parent
        callexpr.
        """
        return Self.parent.cast(T.CallExpr).then(
            lambda ce: If(ce.name == Self, ce, No(CallExpr))
        )

    @langkit_property(return_type=EquationType, dynamic_vars=[env, origin])
    def parent_callexprs_equation(typ=T.BaseTypeDecl.entity):
        """
        Construct the xref equation for the chain of parent nested callexprs.
        """
        return (
            Self.equation_for_type(typ)
            & Self.parent_nested_callexpr.then(
                lambda pce: pce.parent_callexprs_equation(typ.comp_type),
                default_val=LogicTrue()
            )
        )


@abstract
@has_abstract_list
class BasicAssoc(AdaNode):
    expr = AbstractProperty(type=T.Expr, ignore_warn_on_node=True)
    names = AbstractProperty(type=T.AdaNode.array)


class ParamAssoc(BasicAssoc):
    """
    Assocation (X => Y) used for aggregates and parameter associations.
    """
    designator = Field(type=T.AdaNode)
    r_expr = Field(type=T.Expr)

    expr = Property(Self.r_expr)
    names = Property(If(Self.designator.is_null,
                        EmptyArray(AdaNode), Self.designator.singleton))


class AggregateAssoc(BasicAssoc):
    """
    Assocation (X => Y) used for aggregates and parameter associations.
    """
    designators = Field(type=T.AdaNode.list)
    r_expr = Field(type=T.Expr)

    expr = Property(Self.r_expr)
    names = Property(Self.designators.map(lambda d: d))


class MultiDimArrayAssoc(AggregateAssoc):
    pass


class AssocList(BasicAssoc.list):

    @langkit_property()
    def unpacked_params():
        """
        Given the list of ParamAssoc, that can in certain case designate
        several actual parameters at once, create an unpacked list of
        SingleActual instances.
        """
        return Self.mapcat(lambda pa: Let(lambda names=pa.names: If(
            names.length == 0,
            New(SingleActual, name=No(Identifier), assoc=pa).singleton,
            names.filtermap(
                filter_expr=lambda n: n.is_a(T.BaseId),
                expr=lambda i:
                New(SingleActual, name=i.cast(T.BaseId), assoc=pa)
            )
        )))


class DeclList(AdaNode.list):
    pass


class StmtList(AdaNode.list):
    pass


class ExplicitDeref(Name):
    prefix = Field(type=T.Name)
    ref_var = Property(Self.prefix.ref_var)

    @langkit_property()
    def designated_env():
        # Since we have implicit dereference in Ada, everything is directly
        # accessible through the prefix, so we just use the prefix's env.
        return Self.prefix.designated_env()

    @langkit_property()
    def env_elements_impl():
        return origin.bind(Self, Self.prefix.env_elements_impl.filter(
            # Env elements for access derefs need to be of an access type
            lambda e: e.cast(BasicDecl)._.canonical_expr_type.is_access_type
        ))

    @langkit_property()
    def xref_equation():
        return (
            Self.prefix.sub_equation
            # Evaluate the prefix equation

            & Self.ref_var.domain(Self.env_elements)
            # Restrict the domain of the reference to entities that are of an
            # access type.

            & Bind(Self.ref_var, Self.prefix.ref_var)
            # Propagate this constraint upward to the prefix expression

            & Bind(Self.prefix.type_var,
                   Self.type_var,
                   BaseTypeDecl.accessed_type)
            # We don't need to check if the type is an access type, since we
            # already constrained the domain above.
        )


class BoxExpr(Expr):
    pass


class OthersDesignator(AdaNode):
    pass


class IfExpr(Expr):
    cond_expr = Field(type=T.Expr)
    then_expr = Field(type=T.Expr)
    alternatives = Field(type=T.ElsifExprPart.list)
    else_expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return (
            # Construct sub equations for common sub exprs
            Self.cond_expr.sub_equation
            & Self.then_expr.sub_equation

            & If(
                Not(Self.else_expr.is_null),
                # If there is an else, then construct sub equation
                Self.else_expr.sub_equation
                # And bind the then expr's and the else expr's types
                & Bind(Self.then_expr.type_var, Self.else_expr.type_var),

                # If no else, then the then_expression has type bool
                Bind(Self.then_expr.type_var, Self.bool_type)
            ) & Self.alternatives.logic_all(lambda elsif: (
                # Build the sub equations for cond and then exprs
                elsif.cond_expr.sub_equation
                & elsif.then_expr.sub_equation

                # The condition is boolean
                & Bind(elsif.cond_expr.type_var, Self.bool_type)

                # The elsif branch then expr has the same type as Self's
                # then_expr.
                & Bind(Self.then_expr.type_var, elsif.then_expr.type_var)
            )) & Bind(Self.cond_expr.type_var, Self.bool_type)
            & Bind(Self.then_expr.type_var, Self.type_var)
        )


class ElsifExprPart(AdaNode):
    cond_expr = Field(type=T.Expr)
    then_expr = Field(type=T.Expr)


class CaseExpr(Expr):
    expr = Field(type=T.Expr)
    cases = Field(type=T.CaseExprAlternative.list)

    @langkit_property()
    def xref_equation():
        # We solve Self.expr separately because it is not dependent on the rest
        # of the semres.
        ignore(Var(Self.expr.resolve_names))

        return Self.cases.logic_all(lambda alt: (
            alt.choices.logic_all(lambda c: c.match(
                # Expression case
                lambda e=T.Expr:
                Bind(e.type_var, Self.expr.type_val) & e.sub_equation,

                # TODO: Bind other cases: SubtypeIndication and Range
                lambda _: LogicTrue()
            ))

            # Equations for the dependent expressions
            & alt.expr.sub_equation

            # The type of self is the type of each expr. Also, the type of
            # every expr is bound together by the conjunction of this bind for
            # every branch.
            & Bind(Self.type_var, alt.expr.type_var)
        ))


class CaseExprAlternative(Expr):
    choices = Field(type=T.AdaNode.list)
    expr = Field(type=T.Expr)


@abstract
class SingleTokNode(Name):
    tok = Field(type=T.Token)
    relative_name = Property(Self.tok.symbol)

    r_ref_var = UserField(LogicVarType, public=False)
    """
    This field is the logic variable for this node. It is not used directly,
    instead being retrieved via the ref_var property
    """

    ref_var = Property(Self.r_ref_var)

    sym = Property(
        Self.tok.symbol, doc="Shortcut to get the symbol of this node"
    )


@abstract
class BaseId(SingleTokNode):

    @langkit_property()
    def scope():
        elt = Var(env.get(Self.tok).at(0))
        return If(
            Not(elt.is_null) & elt.el.is_a(
                T.PackageDecl, T.PackageBody, T.GenericPackageDecl,
                T.GenericSubpDecl
            ),
            elt.children_env,
            EmptyEnv
        )

    designated_env = Property(Self.designated_env_impl(False))

    @langkit_property(dynamic_vars=[env])
    def designated_env_impl(is_parent_pkg=BoolType):
        """
        Decoupled implementation for designated_env, specifically used by
        DottedName when the parent is a library level package.
        """
        ents = Var(Self.env_elements_baseid(is_parent_pkg))

        return origin.bind(Self, Let(lambda el=ents.at(0).el: If(
            el._.is_package,
            el.cast(BasicDecl).defining_env,
            ents.map(lambda e: e.cast(BasicDecl).defining_env).env_group
        )))

    parent_scope = Property(env)
    relative_name = Property(Self.tok.symbol)

    designated_type_impl = Property(
        # TODO: For correct semantics and xref, we still want to implement
        # correct support, so that references to the incomplete type don't
        # reference the complete type. This is low priority but still needs
        # to be done.
        env.get_sequential(Self.tok, sequential_from=origin)
        .at(0).cast(BaseTypeDecl.entity),
    )

    @langkit_property(return_type=CallExpr, ignore_warn_on_node=True)
    def parent_callexpr():
        """
        If this BaseId is the main symbol qualifying the prefix in a call
        expression, this returns the corresponding CallExpr node. Return null
        otherwise. For example::

            C (12, 15);
            ^ parent_callexpr = <CallExpr>

            A.B.C (12, 15);
                ^ parent_callexpr = <CallExpr>

            A.B.C (12, 15);
              ^ parent_callexpr = null

            C (12, 15);
               ^ parent_callexpr = null
        """
        return Self.parents.take_while(lambda p: Or(
            p.is_a(CallExpr),
            p.is_a(DottedName, BaseId) & p.parent.match(
                lambda pfx=DottedName: pfx.suffix == p,
                lambda ce=CallExpr: ce.name == p,
                lambda _: False
            )
        )).find(lambda p: p.is_a(CallExpr)).cast(CallExpr)

    @langkit_property(dynamic_vars=[env])
    def env_elements_impl():
        return Self.env_elements_baseid(False)

    @langkit_property(dynamic_vars=[env])
    def env_elements_baseid(is_parent_pkg=BoolType):
        """
        Decoupled implementation for env_elements_impl, specifically used by
        designated_env when the parent is a library level package.

        :param is_parent_pkg: Whether the origin of the env request is a
            package or not.
        """
        parent_use_pkg_clause = Var(
            Self.parents.filter(lambda p: p.is_a(UsePackageClause)).at(0)
        )

        # When we are resolving a name as part of an UsePackageClause, make the
        # use clause node itself the reference of the sequential lookup, so
        # that during the designated env lookup, this use clause and all the
        # following ones are ignored. This more correct and avoids an infinite
        # recursion.
        items = Var(env.get_sequential(
            Self.tok,
            recursive=Not(is_parent_pkg),
            sequential_from=parent_use_pkg_clause.then(
                lambda p: p, default_val=Self
            )
        ))
        pc = Var(Self.parent_callexpr)

        def matching_subp(params, subp, subp_spec, env_el):
            # Either the subprogram has is matching the CallExpr's parameters
            return subp_spec.is_matching_param_list(
                params, env_el.info.md.dottable_subp
                # Or the subprogram is parameterless, and the returned
                # component (s) matches the callexpr (s).
            ) | subp.expr_type.then(lambda et: (
                subp_spec.paramless(env_el.info.md)
                & pc.check_type(et)
            ))

        return origin.bind(Self, If(
            pc.is_null,

            # If it is not the main id in a CallExpr: either the name
            # designates something else than a subprogram, either it designates
            # a subprogram that accepts no explicit argument. So filter out
            # other subprograms.
            items.filter(lambda e: (

                # If current item is a library item, we want to check that it
                # is visible from the current unit.
                (Not(e.is_library_item) | Self.has_with_visibility(e.unit))
                # If there is a subp_spec, check that it corresponds to
                # a parameterless subprogram.
                & e.cast_or_raise(BasicDecl).subp_spec_or_null.then(
                    lambda ss: ss.paramless(e.info.md),
                    default_val=True
                )
            )),

            # This identifier is the name for a called subprogram or an array.
            # So only keep:
            # * subprograms for which the actuals match;
            # * arrays for which the number of dimensions match.
            pc.suffix.cast(AssocList).then(lambda params: (
                items.filter(lambda e: e.match(
                    lambda subp=BasicSubpDecl:
                        matching_subp(params, subp, subp.subp_decl_spec, e),

                    lambda subp=SubpBody:
                        matching_subp(params, subp, subp.subp_spec, e),

                    # Type conversion case
                    lambda _=BaseTypeDecl: params.length == 1,

                    # In the case of ObjectDecls/BasicDecls in general, verify
                    # that the callexpr is valid for the given type designator.
                    lambda o=ObjectDecl: pc.check_type(o.expr_type),
                    lambda b=BasicDecl: pc.check_type(b.expr_type),

                    lambda _: False
                ))
            ), default_val=items)
        ))

    @langkit_property()
    def xref_equation():
        return Let(lambda dt=Self.designated_type_impl: If(
            Not(dt.is_null),

            # Type conversion case
            Bind(Self.ref_var, dt) & Bind(Self.type_var, dt),

            # Other cases
            Self.ref_var.domain(Self.env_elements)
            & Bind(Self.ref_var, Self.type_var,
                   BasicDecl.canonical_expr_type)
        ))


class Identifier(BaseId):
    annotations = Annotations(repr_name="Id")


class StringLiteral(BaseId):
    annotations = Annotations(repr_name="Str")

    @langkit_property()
    def xref_equation():
        return Predicate(BaseTypeDecl.is_str_type, Self.type_var)


class EnumLiteralDecl(BasicDecl):
    enum_identifier = Field(type=T.BaseId)

    @langkit_property()
    def canonical_expr_type():
        return Self.parents.find(
            lambda p: p.is_a(BaseTypeDecl)
        ).cast(BaseTypeDecl).as_entity

    defining_names = Property(Self.enum_identifier.cast(T.Name).singleton)

    env_spec = EnvSpec(
        add_to_env=add_to_env_kv(Self.enum_identifier.sym, Self)
    )


class CharLiteral(BaseId):
    annotations = Annotations(repr_name="Chr")

    @langkit_property()
    def xref_equation():
        return Predicate(BaseTypeDecl.is_char_type, Self.type_var)


@abstract
class NumLiteral(SingleTokNode):
    annotations = Annotations(repr_name="Num")


class RealLiteral(NumLiteral):
    annotations = Annotations(repr_name="Real")

    @langkit_property()
    def xref_equation():
        return Predicate(BaseTypeDecl.is_real_type, Self.type_var)


class IntLiteral(NumLiteral):
    annotations = Annotations(repr_name="Int")

    @langkit_property()
    def xref_equation():
        return Predicate(BaseTypeDecl.is_int_type, Self.type_var)


class NullLiteral(SingleTokNode):
    annotations = Annotations(repr_name="Null")


class SingleFormal(Struct):
    name = UserField(type=BaseId)
    spec = UserField(type=BaseFormalParamDecl.entity)


class SingleActual(Struct):
    name = UserField(type=BaseId)
    assoc = UserField(type=T.BasicAssoc)


class ParamMatch(Struct):
    """
    Helper data structure to implement SubpSpec/ParamAssocList matching.

    Each value relates to one ParamAssoc.
    """
    has_matched = UserField(type=BoolType, doc="""
        Whether the matched ParamAssoc a ParamSpec.
    """)
    actual = UserField(type=SingleActual)
    formal = UserField(type=SingleFormal)


@abstract
class BaseSubpSpec(BaseFormalParamHolder):
    name = AbstractProperty(type=T.Name, ignore_warn_on_node=True)
    returns = AbstractProperty(type=T.TypeExpr.entity)

    node_params = AbstractProperty(type=T.ParamSpec.array, public=True)
    params = Property(Self.node_params.map(lambda p: p.as_entity))

    abstract_formal_params = Property(
        Self.params.map(lambda p: p.cast(BaseFormalParamDecl))
    )

    nb_min_params = Property(
        Self.unpacked_formal_params.filter(
            lambda p: p.spec.is_mandatory
        ).length,
        type=LongType, doc="""
        Return the minimum number of parameters this subprogram can be called
        while still being a legal call.
        """
    )

    nb_max_params = Property(
        Self.unpacked_formal_params.length, type=LongType,
        doc="""
        Return the maximum number of parameters this subprogram can be called
        while still being a legal call.
        """
    )

    @langkit_property(return_type=BoolType, dynamic_vars=[env])
    def is_matching_param_list(params=AssocList, is_dottable_subp=BoolType):
        """
        Return whether a AssocList is a match for this SubpSpec, i.e.
        whether the argument count (and designators, if any) match.
        """
        match_list = Var(Self.match_param_list(params, is_dottable_subp))
        nb_max_params = If(is_dottable_subp, Self.nb_max_params - 1,
                           Self.nb_max_params)
        nb_min_params = If(is_dottable_subp, Self.nb_min_params - 1,
                           Self.nb_min_params)

        return And(
            params.length <= nb_max_params,
            match_list.all(lambda m: m.has_matched),
            match_list.filter(
                lambda m: m.formal.spec.is_mandatory
            ).length == nb_min_params,
        )

    @langkit_property(return_type=BoolType)
    def match_signature(other=T.SubpSpec.entity):
        """
        Return whether SubpSpec's signature matches Self's.

        Note that the comparison for types isn't just a name comparison: it
        compares the canonical subtype.
        """
        origin_self = Var(Self.name)
        origin_other = Var(other.el.name)
        return And(
            # Check that the names are the same
            Self.name.matches(other.name),

            # Check that the return type is the same. Caveat: it's not because
            # we could not find the canonical type that it is null!
            #
            # TODO: simplify this code when SubpSpec provides a kind to
            # distinguish functions and procedures.
            If(other.returns.is_null,
               Self.returns.is_null,
               And(Not(other.returns.is_null),
                   origin.bind(origin_other,
                               canonical_type_or_null(other.returns))
                   == origin.bind(origin_self,
                                  canonical_type_or_null(Self.returns)))),

            # Check that there is the same number of formals and that each
            # formal matches.
            Let(
                lambda
                self_params=Self.unpacked_formal_params,
                other_params=other.unpacked_formal_params:

                And(self_params.length == other_params.length,
                    self_params.all(
                        lambda i, p:
                        And(
                            p.name.matches(other_params.at(i).name),
                            origin.bind(
                                origin_self,
                                canonical_type_or_null(p.spec.type_expression)
                            ) == origin.bind(
                                origin_other,
                                canonical_type_or_null(
                                    other_params.at(i).spec.type_expression
                                )
                            )
                        )
                    ))
            )
        )

    @langkit_property(return_type=LexicalEnvType,
                      dynamic_vars=[origin])
    def defining_env():
        """
        Helper for BasicDecl.defining_env.
        """
        return If(Self.returns.is_null, EmptyEnv, Self.returns.defining_env)

    @langkit_property(return_type=BaseTypeDecl.entity, dynamic_vars=[origin])
    def potential_dottable_type():
        """
        If self meets the criterias for being a subprogram callable via the dot
        notation, return the type of dottable elements.
        """
        return Self.params._.at(0)._.type_expr._.element_type

    @langkit_property(return_type=T.BasicDecl.array)
    def dottable_subp():
        """
        Used for environments. Returns either an empty array, or an array
        containg the subprogram declaration for this spec, if self meets the
        criterias for being a dottable subprogram.
        """
        bd = Var(Self.parent.cast_or_raise(BasicDecl))
        return origin.bind(Self, If(
            And(
                Self.nb_max_params > 0,
                Self.potential_dottable_type.then(lambda t: And(
                    # Dot notation only works on tagged types
                    t.is_tagged_type,

                    Or(
                        # Needs to be declared in the same scope as the type
                        t.declarative_scope == bd.declarative_scope,

                        # Or in the private part corresponding to the type's
                        # public part. TODO: This is invalid because it will
                        # make private subprograms visible from the outside.
                        # Fix:
                        #
                        # 1. Add a property that synthetizes a full view node
                        # for a tagged type when there isn't one in the source.
                        #
                        # 2. Add this synthetized full view to the private
                        # part of the package where the tagged type is defined,
                        # if there is one, as part of the tagged type
                        # definition's env spec.
                        #
                        # 3. When computing the private part, if there is a
                        # real in-source full view for the tagged type,
                        # replace the synthetic one.
                        #
                        # 4. Then we can just add the private dottable
                        # subprograms to the private full view.

                        t.declarative_scope == bd.declarative_scope.parent
                        .cast(PackageDecl).then(lambda pd: pd.public_part)
                    )
                ))
            ),
            bd.singleton,
            EmptyArray(T.BasicDecl)
        ))

    @langkit_property(return_type=BoolType)
    def paramless(md=Metadata):
        """
        Utility function. Given a subprogram spec and its associated metadata,
        determine if it can be called without parameters (and hence without a
        callexpr).
        """
        return Or(
            md.dottable_subp & (Self.nb_min_params == 1),
            Self.nb_min_params == 0
        )


class SubpSpec(BaseSubpSpec):
    subp_kind = Field(type=T.SubpKind)
    subp_name = Field(type=T.Name)
    subp_params = Field(type=T.Params)
    subp_returns = Field(type=T.TypeExpr)

    name = Property(Self.subp_name)

    node_params = Property(
        Self.subp_params.then(
            lambda p: p.params.map(lambda p: p),
            default_val=EmptyArray(ParamSpec)
        )
    )
    returns = Property(Self.subp_returns.as_entity)


class EntryDecl(BasicDecl):
    overriding = Field(type=Overriding)
    entry_id = Field(type=T.Identifier)
    family_type = Field(type=T.AdaNode)
    params = Field(type=T.Params)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.entry_id.cast(T.Name).singleton)


class Quantifier(EnumNode):
    alternatives = ["all", "some"]


class IterType(EnumNode):
    alternatives = ["in", "of"]


@abstract
class LoopSpec(AdaNode):
    pass

    @langkit_property(return_type=EquationType,
                      kind=AbstractKind.abstract_runtime_check)
    def xref_equation():
        pass


class ForLoopVarDecl(BasicDecl):
    id = Field(type=T.Identifier)
    id_type = Field(type=T.SubtypeIndication)

    defining_names = Property(Self.id.cast(T.Name).singleton)

    expr_type = Property(If(
        Self.id_type.is_null,

        # The type of a for loop variable does not need to be annotated, it can
        # eventually be infered, which necessitates name resolution on the loop
        # specification. Run resolution if necessary.
        Let(lambda p=If(
            Self.id.type_val.is_null,
            Self.parent.parent.cast(T.ForLoopStmt).resolve_names,
            True
        ): If(p, Self.id.type_val.cast_or_raise(BaseTypeDecl.entity),
              No(BaseTypeDecl.entity))),

        # If there is a type annotation, just return it
        Self.id_type.designated_type.canonical_type
    ))

    env_spec = EnvSpec(
        add_to_env=add_to_env_kv(Self.id.sym, Self)
    )


class ForLoopSpec(LoopSpec):
    var_decl = Field(type=T.ForLoopVarDecl)
    loop_type = Field(type=IterType)
    has_reverse = Field(type=Reverse)
    iter_expr = Field(type=T.AdaNode)

    @langkit_property(return_type=EquationType)
    def xref_equation():
        int = Var(Self.std_entity('Integer'))

        return Self.loop_type.match(

            # This is a for .. in
            lambda _=IterType.alt_in:

            # Let's handle the different possibilities
            Self.iter_expr.match(
                # Anonymous range case: for I in 1 .. 100
                # In that case, the type of everything is Standard.Integer.
                lambda binop=T.BinOp:
                Bind(binop.type_var, int) &
                Bind(binop.left.type_var, int) &
                Bind(binop.right.type_var, int) &
                Bind(Self.var_decl.id.type_var, int),

                # Subtype indication case: the induction variable is of the
                # type.
                lambda t=T.SubtypeIndication:
                Bind(Self.var_decl.id.type_var,
                     t.designated_type.canonical_type),

                # Name case: Either the name is a subtype indication, or an
                # attribute on a subtype indication, in which case the logic is
                # the same as above, either it's an expression that yields an
                # iterator.
                lambda t=T.Name: t.name_designated_type.then(
                    lambda typ:
                    Bind(Self.var_decl.id.type_var, typ.canonical_type),
                    # TODO: Handle the iterator case
                    default_val=LogicTrue()
                ),

                lambda _: LogicTrue()  # should never happen
            ),

            # This is a for .. of
            lambda _=IterType.alt_of:
            # Equation for the expression
            Self.iter_expr.sub_equation

            # Then we want the type of the induction variable to be the
            # component type of the type of the expression.
            & Bind(Self.iter_expr.cast(T.Expr).type_var,
                   Self.var_decl.id.type_var,
                   BaseTypeDecl.comp_type)

            # If there is a type annotation, then the type of var should be
            # conformant.
            & If(Self.var_decl.id_type.is_null,
                 LogicTrue(),
                 Bind(Self.var_decl.id.type_var,
                      Self.var_decl.id_type.designated_type.canonical_type))

            # Finally, we want the type of the expression to be an iterable
            # type.
            & Predicate(BaseTypeDecl.is_iterable_type,
                        Self.iter_expr.cast(T.Expr).type_var)
        )


class QuantifiedExpr(Expr):
    quantifier = Field(type=Quantifier)
    loop_spec = Field(type=T.ForLoopSpec)
    expr = Field(type=T.Expr)


class Allocator(Expr):
    subpool = Field(type=T.Expr)
    type_or_expr = Field(type=T.AdaNode)

    @langkit_property()
    def get_allocated_type():
        return origin.bind(Self, Self.type_or_expr.as_entity.match(
            lambda t=SubtypeIndication.entity: t.designated_type,
            lambda q=QualExpr.entity: q.designated_type,
            lambda _: No(BaseTypeDecl.entity)
        ))

    @langkit_property(return_type=EquationType)
    def xref_equation():
        return (
            Self.type_or_expr.sub_equation
            & Predicate(BaseTypeDecl.matching_allocator_type,
                        Self.type_var, Self.get_allocated_type)
        )


class QualExpr(Name):
    prefix = Field(type=T.Name)
    suffix = Field(type=T.Expr)

    ref_var = Property(Self.prefix.ref_var)

    @langkit_property(return_type=EquationType)
    def xref_equation():
        typ = Self.prefix.designated_type_impl.canonical_type

        return (
            Self.suffix.sub_equation
            & Bind(Self.prefix.ref_var, typ)
            & Bind(Self.prefix.type_var, typ)
            & Bind(Self.suffix.type_var, typ)
            & Bind(Self.type_var, typ)
        )

    # TODO: once we manage to turn prefix into a subtype indication, remove
    # this property and update Allocator.get_allocated type to do:
    # q.prefix.designated_type.
    designated_type = Property(
        env.bind(Self.node_env, origin.bind(Self, Self.designated_type_impl)),
    )
    designated_type_impl = Property(Self.prefix.designated_type_impl)


class AttributeRef(Name):
    prefix = Field(type=T.Name)
    attribute = Field(type=T.Identifier)
    args = Field(type=T.AdaNode)

    ref_var = Property(Self.prefix.ref_var)

    designated_type_impl = Property(
        If(Self.attribute.sym == 'Class',
           Self.prefix.designated_type_impl.classwide_type,
           Self.prefix.designated_type_impl)
    )


class UpdateAttributeRef(AttributeRef):
    pass


class RaiseExpr(Expr):
    exception_name = Field(type=T.Expr)
    error_message = Field(type=T.Expr)


class DottedName(Name):
    prefix = Field(type=T.Name)
    suffix = Field(type=T.BaseId)
    ref_var = Property(Self.suffix.ref_var)

    @langkit_property()
    def designated_env():
        pfx_env = Var(Self.prefix.designated_env)
        return env.bind(pfx_env, If(
            pfx_env.env_node._.is_library_package & Self.suffix.is_a(T.BaseId),
            Self.suffix.designated_env_impl(True),
            Self.suffix.designated_env
        ))

    scope = Property(Self.suffix.then(
        lambda sfx: env.bind(Self.parent_scope, sfx.scope),
        default_val=EmptyEnv
    ))

    parent_scope = Property(Self.prefix.scope)

    relative_name = Property(Self.suffix.relative_name)

    @langkit_property()
    def env_elements_impl():
        pfx_env = Var(origin.bind(Self, Self.prefix.designated_env))

        return env.bind(pfx_env, If(
            pfx_env.env_node._.is_library_package & Self.suffix.is_a(T.BaseId),
            Self.suffix.env_elements_baseid(True),
            Self.suffix.env_elements_impl
        ))

    designated_type_impl = Property(lambda: (
        env.bind(Self.prefix.designated_env,
                 Self.suffix.designated_type_impl)
    ))

    @langkit_property()
    def xref_equation():
        dt = Self.designated_type_impl
        base = Var(
            Self.prefix.sub_equation
            & env.bind(Self.prefix.designated_env, Self.suffix.sub_equation)
        )
        return If(
            Not(dt.is_null),
            base,
            base & Self.env_elements.logic_any(lambda e: (
                Bind(Self.suffix.ref_var, e)
                & e.cast(BasicDecl.entity).constrain_prefix(Self.prefix)
                & Bind(Self.type_var, Self.suffix.type_var)
            ))
        )


class CompilationUnit(AdaNode):
    """Root node for all Ada analysis units."""
    prelude = Field(doc="``with``, ``use`` or ``pragma`` statements.")
    body = Field(type=T.AdaNode)
    pragmas = Field(type=T.Pragma.list)

    env_spec = EnvSpec(env_hook_arg=Self)


class SubpBody(Body):
    env_spec = EnvSpec(
        initial_env=env.bind(Self.initial_env, If(
            Self.is_library_item,
            # In case the subp spec for this library level subprogram is
            # missing, we'll put it in the parent's scope. This way, the xref
            # to it should still resolve.
            Self.subp_spec.name.scope._or(Self.subp_spec.name.parent_scope),
            Self.parent.children_env
        )),
        add_env=True,
        add_to_env=[
            # Add the body to its own parent env
            add_to_env_kv(Self.relative_name, Self),

            # Add the __body link to the spec, if there is one
            add_to_env_kv(
                '__body', Self,
                dest_env=Self.decl_part.then(
                    lambda d: d.children_env,
                    default_val=EmptyEnv
                ),
                is_post=True
            ),
        ],
        ref_envs=[ref_used_packages(), ref_generic_formals(), ref_std()],
        env_hook_arg=Self,
    )

    overriding = Field(type=Overriding)
    subp_spec = Field(type=T.SubpSpec)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_id = Field(type=T.Name)

    defining_names = Property(Self.subp_spec.name.singleton)
    defining_env = Property(Self.subp_spec.defining_env)

    decl_part = Property(If(
        Self.is_library_item,

        # If library item, we just return the spec. We don't check if it's
        # a valid and matching subprogram because that's an error case.
        get_library_item(Self.spec_unit),

        # If not a library item, find the matching subprogram spec in the
        # env.
        Self.parent.node_env.get(Self.relative_name)
        .find(lambda sp: sp.match(
            # If this body completes a generic subprogram, then we just return
            # it (no need to match the signature).
            lambda _=T.GenericSubpDecl: True,

            lambda subp_decl=T.BasicSubpDecl:
            subp_decl.subp_decl_spec.match_signature(Self.subp_spec.as_entity),

            lambda _: False
        )).el
    ),
        public=True,
        doc="Return the SubpDecl corresponding to this node.",
        ignore_warn_on_node=True
    )


class HandledStmts(AdaNode):
    stmts = Field(type=T.AdaNode.list)
    exceptions = Field(type=T.AdaNode.list)


class ExceptionHandler(AdaNode):
    exc_name = Field(type=T.Identifier)
    handled_exceptions = Field(type=T.AdaNode.list)
    stmts = Field(type=T.AdaNode.list)


@abstract
class Stmt(AdaNode):
    xref_entry_point = Property(True)


@abstract
class SimpleStmt(Stmt):
    pass


@abstract
class CompositeStmt(Stmt):
    pass


class CallStmt(SimpleStmt):
    """
    Statement for entry or procedure calls.
    """
    call = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return (
            Self.call.sub_equation

            # Call statements can have no return value
            & Bind(Self.call.type_var, No(AdaNode.entity))
        )


class NullStmt(SimpleStmt):
    null_lit = Field(repr=False)

    @langkit_property()
    def xref_equation():
        return LogicTrue()


class AssignStmt(SimpleStmt):
    dest = Field(type=T.Expr)
    expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return (
            Self.dest.sub_equation
            & Self.expr.sub_equation
            & Bind(Self.expr.type_var, Self.dest.type_var,
                   eq_prop=BaseTypeDecl.matching_assign_type)
        )


class GotoStmt(SimpleStmt):
    label_name = Field(type=T.Name)

    @langkit_property()
    def xref_equation():
        return Self.label_name.sub_equation


class ExitStmt(SimpleStmt):
    loop_name = Field(type=T.Identifier)
    condition = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return (
            Self.condition.sub_equation
            & Bind(Self.condition.type_var, Self.bool_type)
        )


class ReturnStmt(SimpleStmt):
    return_expr = Field(type=T.Expr)

    subp = Property(
        Self.parents.find(lambda p: p.is_a(SubpBody)).cast(SubpBody).as_entity,
        doc="Returns the subprogram this return statement belongs to"
    )

    @langkit_property()
    def xref_equation():
        return (
            Self.return_expr.sub_equation
            & Bind(
                Self.return_expr.type_var,
                Self.subp.subp_spec.returns.designated_type.canonical_type
            )
        )


class RequeueStmt(SimpleStmt):
    call_name = Field(type=T.Expr)
    has_abort = Field(type=Abort)


class AbortStmt(SimpleStmt):
    names = Field(type=T.Name.list)

    @langkit_property()
    def xref_equation():
        return Self.names.logic_all(
            lambda name:
            name.sub_equation
            & Predicate(BaseTypeDecl.is_task_type,
                        name.type_var)
        )


class DelayStmt(SimpleStmt):
    has_until = Field(type=Until)
    expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return Self.expr.sub_equation & Bind(
            Self.expr.type_var, Self.std_entity('Duration')
        )


class RaiseStmt(SimpleStmt):
    exception_name = Field(type=T.Expr)
    error_message = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return Self.exception_name.sub_equation


class IfStmt(CompositeStmt):
    cond_expr = Field(type=T.Expr)
    then_stmts = Field(type=T.AdaNode.list)
    alternatives = Field(type=T.ElsifStmtPart.list)
    else_stmts = Field(type=T.AdaNode.list)

    @langkit_property()
    def xref_equation():
        return (
            Self.cond_expr.sub_equation
            & Bind(Self.cond_expr.type_var, Self.bool_type)
            & Self.alternatives.logic_all(
                lambda elsif: elsif.cond_expr.sub_equation
                & Bind(elsif.cond_expr.type_var, Self.bool_type)
            )
        )


class ElsifStmtPart(AdaNode):
    cond_expr = Field(type=T.Expr)
    stmts = Field(type=T.AdaNode.list)


class LabelDecl(BasicDecl):
    name = Field(type=T.Identifier)
    env_spec = EnvSpec(add_to_env=add_to_env_kv(Self.name.sym, Self))
    defining_names = Property(Self.name.cast(T.Name).singleton)


class Label(SimpleStmt):
    decl = Field(type=T.LabelDecl)


class WhileLoopSpec(LoopSpec):
    expr = Field(type=T.Expr)

    @langkit_property(return_type=EquationType)
    def xref_equation():
        return Self.expr.sub_equation & (
            Bind(Self.expr.type_var, Self.bool_type)
        )


class NamedStmtDecl(BasicDecl):
    """
    BasicDecl that is always the declaration inside a named statement.
    """
    name = Field(type=T.Identifier)
    defining_names = Property(Self.name.cast(T.Name).singleton)
    defining_env = Property(Self.parent.cast(T.NamedStmt).stmt.children_env)


class NamedStmt(CompositeStmt):
    """
    Wrapper class, used for composite statements that can be named (declare
    blocks, loops). This allows to both have a BasicDecl for the named entity
    declared, and a CompositeStmt for the statement hierarchy.
    """
    decl = Field(type=T.NamedStmtDecl)
    stmt = Field(type=T.CompositeStmt)

    env_spec = EnvSpec(
        add_env=True,
        add_to_env=add_to_env_kv(Self.decl.name.sym, Self.decl)
    )


@abstract
class BaseLoopStmt(CompositeStmt):
    spec = Field(type=T.LoopSpec)
    stmts = Field(type=T.AdaNode.list)
    end_id = Field(type=T.Identifier)

    @langkit_property(return_type=EquationType)
    def xref_equation():
        return Self.spec.xref_equation


class LoopStmt(BaseLoopStmt):
    pass


class ForLoopStmt(BaseLoopStmt):
    pass


class WhileLoopStmt(BaseLoopStmt):
    pass


@abstract
class BlockStmt(CompositeStmt):
    env_spec = EnvSpec(add_env=True)


class DeclBlock(BlockStmt):
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_id = Field(type=T.Identifier)


class BeginBlock(BlockStmt):
    stmts = Field(type=T.HandledStmts)
    end_id = Field(type=T.Identifier)


class ExtendedReturnStmt(CompositeStmt):
    object_decl = Field(type=T.ObjectDecl)
    stmts = Field(type=T.HandledStmts)

    @langkit_property(return_type=EquationType)
    def xref_equation():
        return LogicTrue()

    env_spec = EnvSpec(add_env=True)


class CaseStmt(CompositeStmt):
    case_expr = Field(type=T.Expr)
    case_alts = Field(type=T.CaseStmtAlternative.list)


class CaseStmtAlternative(AdaNode):
    choices = Field(type=T.AdaNode.list)
    stmts = Field(type=T.AdaNode.list)


class AcceptStmt(CompositeStmt):
    name = Field(type=T.Identifier)
    entry_index_expr = Field(type=T.Expr)
    params = Field(type=T.Params)


class AcceptStmtWithStmts(AcceptStmt):
    stmts = Field(type=T.HandledStmts)
    end_name = Field(type=T.Name)


class SelectStmt(CompositeStmt):
    guards = Field(type=T.SelectWhenPart.list)
    else_stmts = Field(type=T.AdaNode.list)
    abort_stmts = Field(type=T.AdaNode.list)


class SelectWhenPart(AdaNode):
    choices = Field(type=T.Expr)
    stmts = Field(type=T.AdaNode.list)


class TerminateAlternative(SimpleStmt):
    pass


class PackageBody(Body):
    env_spec = child_unit('__body', Self.body_scope)

    package_name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_id = Field(type=T.Name)

    defining_names = Property(Self.package_name.singleton)
    defining_env = Property(Self.children_env.env_orphan)

    @langkit_property(dynamic_vars=[env])
    def body_scope():
        scope = Var(env.bind(Self.parent.node_env, Self.package_name.scope))
        public_scope = Var(scope.env_node.cast(T.GenericPackageDecl).then(
            lambda gen_pkg_decl: gen_pkg_decl.package_decl.children_env,
            default_val=scope
        ))

        # If the package has a private part, then get the private part,
        # else return the public part.
        return public_scope.get('__privatepart', recursive=False).at(0).then(
            lambda pp: pp.children_env, default_val=public_scope
        )

    @langkit_property(return_type=T.BasePackageDecl, public=True,
                      ignore_warn_on_node=True)
    def decl_part():
        """
        Return the BasePackageDecl corresponding to this node.

        If the case of generic package declarations, this returns the
        "package_decl" field instead of the GenericPackageDecl itself.
        """
        return env.bind(
            Self.parent.node_env,
            Self.package_name.matching_nodes_impl.at(0).match(
                lambda pkg_decl=T.PackageDecl: pkg_decl,
                lambda gen_pkg_decl=T.GenericPackageDecl:
                    gen_pkg_decl.package_decl,
                lambda _: No(T.BasePackageDecl)
            )
        )


class TaskBody(Body):
    name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_name = Field(type=T.Name)

    defining_names = Property(Self.name.singleton)


class ProtectedBody(Body):
    name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    end_name = Field(type=T.Name)

    defining_names = Property(Self.name.singleton)


class EntryBody(Body):
    entry_name = Field(type=T.Identifier)
    index_spec = Field(type=T.EntryIndexSpec)
    params = Field(type=T.Params)
    barrier = Field(type=T.Expr)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_name = Field(type=T.Name)

    defining_names = Property(Self.entry_name.cast(Name).singleton)


class EntryIndexSpec(AdaNode):
    id = Field(type=T.Identifier)
    subtype = Field(type=T.AdaNode)


class Subunit(AdaNode):
    name = Field(type=T.Name)
    body = Field(type=T.Body)


class ProtectedBodyStub(BodyStub):
    name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class SubpBodyStub(BodyStub):
    overriding = Field(type=Overriding)
    subp_spec = Field(type=T.SubpSpec)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.subp_spec.name.singleton)
    # Note that we don't have to override the defining_env property here since
    # what we put in lexical environment is their SubpSpec child.


class PackageBodyStub(BodyStub):
    name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class TaskBodyStub(BodyStub):
    name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class LibraryItem(AdaNode):
    has_private = Field(type=Private)
    item = Field(type=T.BasicDecl)


class RangeSpec(AdaNode):
    range = Field(type=Expr)


class IncompleteTypeDecl(BaseTypeDecl):
    discriminants = Field(type=T.DiscriminantPart)


class IncompleteTaggedTypeDecl(IncompleteTypeDecl):
    has_abstract = Field(type=Abstract)


class Params(AdaNode):
    params = Field(type=ParamSpec.list)


class ParentList(Name.list):
    pass


class DiscriminantChoiceList(Identifier.list):
    pass


class AlternativesList(AdaNode.list):
    pass


class ConstraintList(AdaNode.list):
    pass


class UnconstrainedArrayIndex(AdaNode):
    subtype_indication = Field(type=SubtypeIndication)

    @langkit_property(dynamic_vars=[origin])
    def designated_type():
        return Self.subtype_indication.designated_type
