# PolicyEngine UK — Live API Reference

This reference is generated from the installed `policyengine_uk_compiled` library at deploy time. Treat it as the authoritative source for function signatures, parameter schemas, and engine capabilities.

## Engine capabilities snapshot

Output of `pe.capabilities()` at build time:

```json
{
  "engine": "PolicyEngine UK compiled microsimulation engine",
  "fiscal_years_supported": "1994\u20132029 (year=2025 means 2025/26 fiscal year)",
  "multi_year_analysis": "Fully supported. Call tools once per year and collate results. Never refuse a multi-year or trend question \u2014 just loop over years.",
  "datasets": {
    "frs": {
      "description": "Family Resources Survey. Full tax-benefit model, ~20,000 households. Available from 1994 to present. Use for historical analysis (pre-2023) or to cross-check EFRS estimates.",
      "locally_cached_years": []
    },
    "efrs": {
      "description": "Enhanced Family Resources Survey. Gold standard for distributional analysis. Merges FRS household microdata with Wealth and Assets Survey (wealth) and Living Costs and Food Survey (expenditure). Full tax-benefit model. Available from 2023.",
      "locally_cached_years": []
    },
    "lcfs": {
      "description": "Living Costs and Food Survey. Expenditure and consumption data. Use for VAT, duties, or consumption-based tax analysis.",
      "locally_cached_years": []
    },
    "spi": {
      "description": "Survey of Personal Incomes (HMRC administrative data). Person-level only \u2014 no household or benefit calculations. Far better coverage of very high earners (top 1\u20135%). Use when the question is specifically about high-income taxpayers or income tax/NI only.",
      "locally_cached_years": []
    },
    "was": {
      "description": "Wealth and Assets Survey. Authoritative source for wealth distribution. Use for wealth tax, inheritance, or asset-based analysis.",
      "locally_cached_years": []
    }
  },
  "default_dataset": "efrs",
  "programmes_modelled": [
    "Income tax",
    "National Insurance (employee and employer)",
    "Universal Credit",
    "Child Benefit",
    "State Pension",
    "Pension Credit",
    "Housing Benefit",
    "Tax Credits (CTC/WTC)",
    "Scottish Child Payment",
    "Benefit Cap",
    "Stamp Duty",
    "Capital Gains Tax",
    "Wealth Tax (parametric)"
  ],
  "microdata_columns_available": {
    "persons": [
      "age",
      "gender",
      "employment_income",
      "self_employment_income",
      "pension_income",
      "capital_gains",
      "savings_interest",
      "baseline_income_tax",
      "reform_income_tax",
      "baseline_employee_ni",
      "reform_employee_ni",
      "baseline_total_income",
      "reform_total_income",
      "weight",
      "region",
      "is_household_head",
      "is_benunit_head",
      "household_id",
      "benunit_id"
    ],
    "benunits": [
      "baseline_universal_credit",
      "reform_universal_credit",
      "baseline_child_benefit",
      "reform_child_benefit",
      "baseline_housing_benefit",
      "reform_housing_benefit",
      "baseline_child_tax_credit",
      "reform_child_tax_credit",
      "baseline_working_tax_credit",
      "reform_working_tax_credit",
      "baseline_pension_credit",
      "reform_pension_credit",
      "baseline_total_benefits",
      "reform_total_benefits",
      "weight",
      "household_id"
    ],
    "households": [
      "baseline_net_income",
      "reform_net_income",
      "baseline_total_tax",
      "reform_total_tax",
      "baseline_total_benefits",
      "reform_total_benefits",
      "baseline_gross_income",
      "rent",
      "council_tax",
      "main_residence_value",
      "region",
      "weight",
      "household_id"
    ]
  },
  "notes": [
    "Rent is an input field on households (rent_monthly). The FRS records actual rent paid, so rent burden (rent/income) can be computed directly from microdata across any year 1994\u20132026.",
    "Poverty and HBAI fields (relative/absolute poverty rates, mean/median equivalised income) are only available from run_economy_simulation, not from analyse_microdata.",
    "EFRS is only available from 2023. For earlier years use FRS."
  ]
}
```

## Public API

### `BENUNIT_DEFAULTS` — dict

dict() -> new empty dictionary
dict(mapping) -> new dictionary initialized from a mapping object's
    (key, value) pairs
dict(iterable) -> new dictionary initialized as if via:
    d = {}
    for k, v in iterable:
        d[k] = v
dict(**kwargs) -> new dictionary initialized with the name=value pairs
    in the keyword argument list.  For example:  dict(one=1, two=2)

### `BenefitCapParams` — ModelMetaclass

```python
BenefitCapParams(*, single_london: Optional[float] = None, single_outside_london: Optional[float] = None, non_single_london: Optional[float] = None, non_single_outside_london: Optional[float] = None, earnings_exemption_threshold: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `BudgetaryImpact` — ModelMetaclass

```python
BudgetaryImpact(*, baseline_revenue: float, reform_revenue: float, revenue_change: float, baseline_benefits: float, reform_benefits: float, benefit_spending_change: float, net_cost: float) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `CapitalGainsTaxParams` — ModelMetaclass

```python
CapitalGainsTaxParams(*, annual_exempt_amount: Optional[float] = None, basic_rate: Optional[float] = None, higher_rate: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `Caseloads` — ModelMetaclass

```python
Caseloads(*, income_tax_payers: float, ni_payers: float, employer_ni_payers: float, universal_credit: float, child_benefit: float, state_pension: float, pension_credit: float, housing_benefit: float, child_tax_credit: float, working_tax_credit: float, income_support: float, esa_income_related: float, jsa_income_based: float, carers_allowance: float, scottish_child_payment: float, benefit_cap_affected: float) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `ChildBenefitParams` — ModelMetaclass

```python
ChildBenefitParams(*, eldest_weekly: Optional[float] = None, additional_weekly: Optional[float] = None, hicbc_threshold: Optional[float] = None, hicbc_taper_end: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `DATASETS` — tuple

Built-in immutable sequence.

If no argument is given, the constructor returns an empty tuple.
If iterable is specified the tuple is initialized from iterable's items.

If the argument is a tuple, the return value is the same object.

### `DecileImpact` — ModelMetaclass

```python
DecileImpact(*, decile: int, avg_baseline_income: Optional[float] = None, avg_reform_income: Optional[float] = None, avg_change: Optional[float] = None, pct_change: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `HOUSEHOLD_DEFAULTS` — dict

dict() -> new empty dictionary
dict(mapping) -> new dictionary initialized from a mapping object's
    (key, value) pairs
dict(iterable) -> new dictionary initialized as if via:
    d = {}
    for k, v in iterable:
        d[k] = v
dict(**kwargs) -> new dictionary initialized with the name=value pairs
    in the keyword argument list.  For example:  dict(one=1, two=2)

### `HbaiIncomes` — ModelMetaclass

```python
HbaiIncomes(*, mean_equiv_bhc: float, mean_equiv_ahc: float, mean_bhc: float, mean_ahc: float, median_equiv_bhc: float, median_equiv_ahc: float) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `HousingBenefitParams` — ModelMetaclass

```python
HousingBenefitParams(*, withdrawal_rate: Optional[float] = None, personal_allowance_single_under25: Optional[float] = None, personal_allowance_single_25_plus: Optional[float] = None, personal_allowance_couple: Optional[float] = None, child_allowance: Optional[float] = None, family_premium: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `IncomeBreakdown` — ModelMetaclass

```python
IncomeBreakdown(*, employment_income: float, self_employment_income: float, pension_income: float, savings_interest_income: float, dividend_income: float, property_income: float, other_income: float) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `IncomeTaxParams` — ModelMetaclass

```python
IncomeTaxParams(*, personal_allowance: Optional[float] = None, pa_taper_threshold: Optional[float] = None, pa_taper_rate: Optional[float] = None, uk_brackets: Optional[list[policyengine_uk_compiled.models.TaxBracket]] = None, scottish_brackets: Optional[list[policyengine_uk_compiled.models.TaxBracket]] = None, dividend_allowance: Optional[float] = None, dividend_basic_rate: Optional[float] = None, dividend_higher_rate: Optional[float] = None, dividend_additional_rate: Optional[float] = None, savings_starter_rate_band: Optional[float] = None, marriage_allowance_max_fraction: Optional[float] = None, marriage_allowance_rounding: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `LabourSupplyParams` — ModelMetaclass

```python
LabourSupplyParams(*, enabled: Optional[bool] = None, subst_married_women_no_children: Optional[float] = None, subst_married_women_child_0_2: Optional[float] = None, subst_married_women_child_3_4: Optional[float] = None, subst_married_women_child_5_10: Optional[float] = None, subst_married_women_child_11_plus: Optional[float] = None, subst_lone_parents_child_0_4: Optional[float] = None, subst_lone_parents_child_5_10: Optional[float] = None, subst_lone_parents_child_11_18: Optional[float] = None, subst_men_and_single_women: Optional[float] = None, income_married_women_no_children: Optional[float] = None, income_married_women_child_0_2: Optional[float] = None, income_married_women_child_3_4: Optional[float] = None, income_married_women_child_5_10: Optional[float] = None, income_married_women_child_11_plus: Optional[float] = None, income_lone_parents_child_0_4: Optional[float] = None, income_lone_parents_child_5_10: Optional[float] = None, income_lone_parents_child_11_18: Optional[float] = None, income_men_and_single_women: Optional[float] = None) -> None
```

OBR labour supply elasticities (Slutsky decomposition).

Source: OBR (2023) "Costing a cut in National Insurance contributions:
the impact on labour supply"
https://obr.uk/docs/dlm_uploads/NICS-Cut-Impact-on-Labour-Supply-Note.pdf

Set `enabled=False` to suppress labour supply responses. All elasticity
fields are optional; omitted fields retain OBR defaults.

### `MicrodataResult` — ModelMetaclass

```python
MicrodataResult(*, persons: Any, benunits: Any, households: Any) -> None
```

Per-entity simulation results as DataFrames.

### `NationalInsuranceParams` — ModelMetaclass

```python
NationalInsuranceParams(*, primary_threshold_annual: Optional[float] = None, upper_earnings_limit_annual: Optional[float] = None, main_rate: Optional[float] = None, additional_rate: Optional[float] = None, secondary_threshold_annual: Optional[float] = None, employer_rate: Optional[float] = None, class2_flat_rate_weekly: Optional[float] = None, class2_small_profits_threshold: Optional[float] = None, class4_lower_profits_limit: Optional[float] = None, class4_upper_profits_limit: Optional[float] = None, class4_main_rate: Optional[float] = None, class4_additional_rate: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `PERSON_DEFAULTS` — dict

dict() -> new empty dictionary
dict(mapping) -> new dictionary initialized from a mapping object's
    (key, value) pairs
dict(iterable) -> new dictionary initialized as if via:
    d = {}
    for k, v in iterable:
        d[k] = v
dict(**kwargs) -> new dictionary initialized with the name=value pairs
    in the keyword argument list.  For example:  dict(one=1, two=2)

### `Parameters` — ModelMetaclass

```python
Parameters(*, fiscal_year: Optional[str] = None, income_tax: Optional[policyengine_uk_compiled.models.IncomeTaxParams] = None, national_insurance: Optional[policyengine_uk_compiled.models.NationalInsuranceParams] = None, universal_credit: Optional[policyengine_uk_compiled.models.UniversalCreditParams] = None, child_benefit: Optional[policyengine_uk_compiled.models.ChildBenefitParams] = None, state_pension: Optional[policyengine_uk_compiled.models.StatePensionParams] = None, pension_credit: Optional[policyengine_uk_compiled.models.PensionCreditParams] = None, benefit_cap: Optional[policyengine_uk_compiled.models.BenefitCapParams] = None, housing_benefit: Optional[policyengine_uk_compiled.models.HousingBenefitParams] = None, tax_credits: Optional[policyengine_uk_compiled.models.TaxCreditsParams] = None, scottish_child_payment: Optional[policyengine_uk_compiled.models.ScottishChildPaymentParams] = None, uc_migration: Optional[policyengine_uk_compiled.models.UcMigrationRates] = None, disability_premiums: Optional[policyengine_uk_compiled.models.DisabilityPremiumParams] = None, income_related_benefits: Optional[policyengine_uk_compiled.models.IncomeRelatedBenefitParams] = None, capital_gains_tax: Optional[policyengine_uk_compiled.models.CapitalGainsTaxParams] = None, stamp_duty: Optional[policyengine_uk_compiled.models.StampDutyParams] = None, wealth_tax: Optional[policyengine_uk_compiled.models.WealthTaxParams] = None, labour_supply: Optional[policyengine_uk_compiled.models.LabourSupplyParams] = None) -> None
```

Full parameter set. All fields optional for use as reform overlay.

### `PensionCreditParams` — ModelMetaclass

```python
PensionCreditParams(*, standard_minimum_single: Optional[float] = None, standard_minimum_couple: Optional[float] = None, savings_credit_threshold_single: Optional[float] = None, savings_credit_threshold_couple: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `PovertyHeadcounts` — ModelMetaclass

```python
PovertyHeadcounts(*, relative_bhc_children: float, relative_bhc_working_age: float, relative_bhc_pensioners: float, relative_ahc_children: float, relative_ahc_working_age: float, relative_ahc_pensioners: float, absolute_bhc_children: float, absolute_bhc_working_age: float, absolute_bhc_pensioners: float, absolute_ahc_children: float, absolute_ahc_working_age: float, absolute_ahc_pensioners: float) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `ProgramBreakdown` — ModelMetaclass

```python
ProgramBreakdown(*, income_tax: float, hicbc: float = 0.0, employee_ni: float, employer_ni: float, vat: float = 0.0, fuel_duty: float = 0.0, alcohol_duty: float = 0.0, tobacco_duty: float = 0.0, capital_gains_tax: float = 0.0, stamp_duty: float = 0.0, wealth_tax: float = 0.0, council_tax: float = 0.0, universal_credit: float, child_benefit: float, state_pension: float, pension_credit: float, housing_benefit: float, child_tax_credit: float, working_tax_credit: float, income_support: float, esa_income_related: float, jsa_income_based: float, carers_allowance: float, scottish_child_payment: float, benefit_cap_reduction: float, passthrough_benefits: float) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `ScottishChildPaymentParams` — ModelMetaclass

```python
ScottishChildPaymentParams(*, weekly_amount: Optional[float] = None, max_age: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `Simulation` — type

```python
Simulation(year: 'int' = 2025, *, persons=None, benunits=None, households=None, data_dir: 'Optional[Union[str, Path]]' = None, dataset: 'Optional[str]' = None, clean_frs_base: 'Optional[str]' = None, clean_frs: 'Optional[str]' = None, frs_raw: 'Optional[str]' = None, binary_path: 'Optional[str]' = None)
```

Run the PolicyEngine UK microsimulation engine.

Accepts data via DataFrames (piped to binary stdin), file paths, or
legacy FRS-specific arguments.

Usage::

    from policyengine_uk_compiled import Simulation, Parameters, IncomeTaxParams

    # From DataFrames (hypothetical household)
    persons, benunits, households = Simulation.single_person(
        employment_income=50000
    )
    sim = Simulation(year=2025, persons=persons, benunits=benunits,
                     households=households)
    result = sim.run()

    # From a data directory
    sim = Simulation(year=2025, data_dir="data/frs/2023")
    result = sim.run()

    # With a parametric reform
    reform = Parameters(income_tax=IncomeTaxParams(personal_allowance=20000))
    result = sim.run(policy=reform)

    # With a structural reform (pre-hook: mutate inputs before simulation)
    from policyengine_uk_compiled import StructuralReform

    def cap_wages(year, persons, benunits, households):
        persons["employment_income"] = persons["employment_income"].clip(upper=100_000)
        return persons, benunits, households

    result = sim.run(structural=StructuralReform(pre=cap_wages))

    # With a structural reform (post-hook: adjust outputs after simulation)
    def add_ubi(year, persons, benunits, households):
        ubi = 50 * 52  # £50/wk per adult
        adults = persons["age"] >= 18
        adult_counts = persons[adults].groupby("household_id").size()
        households["reform_net_income"] += households["household_id"].map(adult_counts).fillna(0) * ubi
        households["reform_total_tax"] = households["baseline_total_tax"]  # unchanged
        return persons, benunits, households

    result = sim.run(structural=StructuralReform(post=add_ubi))

### `SimulationConfig` — ModelMetaclass

```python
SimulationConfig(*, year: int = 2025, policy: Optional[policyengine_uk_compiled.models.Parameters] = None, clean_frs_base: Optional[str] = None, clean_frs: Optional[str] = None, frs_raw: Optional[str] = None, binary_path: Optional[str] = None) -> None
```

Configuration for running a simulation.

### `SimulationResult` — ModelMetaclass

```python
SimulationResult(*, fiscal_year: str, budgetary_impact: policyengine_uk_compiled.models.BudgetaryImpact, income_breakdown: policyengine_uk_compiled.models.IncomeBreakdown, program_breakdown: policyengine_uk_compiled.models.ProgramBreakdown, caseloads: policyengine_uk_compiled.models.Caseloads, decile_impacts: list[policyengine_uk_compiled.models.DecileImpact], winners_losers: policyengine_uk_compiled.models.WinnersLosers, baseline_hbai_incomes: policyengine_uk_compiled.models.HbaiIncomes, reform_hbai_incomes: policyengine_uk_compiled.models.HbaiIncomes, baseline_poverty: policyengine_uk_compiled.models.PovertyHeadcounts, reform_poverty: policyengine_uk_compiled.models.PovertyHeadcounts, cpi_index: float) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `StampDutyBand` — ModelMetaclass

```python
StampDutyBand(*, rate: float, threshold: float) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `StampDutyParams` — ModelMetaclass

```python
StampDutyParams(*, bands: Optional[list[policyengine_uk_compiled.models.StampDutyBand]] = None, annual_purchase_probability: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `StatePensionParams` — ModelMetaclass

```python
StatePensionParams(*, new_state_pension_weekly: Optional[float] = None, old_basic_pension_weekly: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `StructuralReform` — type

```python
StructuralReform(pre: 'Optional[HookFn]' = None, post: 'Optional[HookFn]' = None) -> None
```

Container for pre- and post-simulation structural reform hooks.

Both hooks are optional.  Omit whichever you don't need.

Hook signature (same for pre and post):

    def hook(
        year: int,
        persons: pd.DataFrame,
        benunits: pd.DataFrame,
        households: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        ...
        return persons, benunits, households

Example — add a £50/wk UBI to every adult's reform net income::

    def ubi_post(year, persons, benunits, households):
        ubi_annual = 50 * 52
        mask = persons["age"] >= 18
        persons.loc[mask, "reform_income_tax"] = 0  # illustrative
        households["reform_net_income"] += ubi_annual  # per-household
        return persons, benunits, households

    reform = StructuralReform(post=ubi_post)

Example — replace employment income with a flat wage in 2025 only::

    def flat_wage_pre(year, persons, benunits, households):
        if year == 2025:
            persons["employment_income"] = persons["employment_income"].clip(upper=50_000)
        return persons, benunits, households

    reform = StructuralReform(pre=flat_wage_pre)

### `TaxCreditsParams` — ModelMetaclass

```python
TaxCreditsParams(*, wtc_basic_element: Optional[float] = None, wtc_couple_element: Optional[float] = None, wtc_lone_parent_element: Optional[float] = None, wtc_30_hour_element: Optional[float] = None, ctc_child_element: Optional[float] = None, ctc_family_element: Optional[float] = None, ctc_disabled_child_element: Optional[float] = None, ctc_severely_disabled_child_element: Optional[float] = None, income_threshold: Optional[float] = None, taper_rate: Optional[float] = None, wtc_min_hours_single: Optional[float] = None, wtc_min_hours_couple: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `UcMigrationRates` — ModelMetaclass

```python
UcMigrationRates(*, housing_benefit: Optional[float] = None, tax_credits: Optional[float] = None, income_support: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `UniversalCreditParams` — ModelMetaclass

```python
UniversalCreditParams(*, standard_allowance_single_under25: Optional[float] = None, standard_allowance_single_over25: Optional[float] = None, standard_allowance_couple_under25: Optional[float] = None, standard_allowance_couple_over25: Optional[float] = None, child_element_first: Optional[float] = None, child_element_subsequent: Optional[float] = None, disabled_child_lower: Optional[float] = None, disabled_child_higher: Optional[float] = None, lcwra_element: Optional[float] = None, carer_element: Optional[float] = None, taper_rate: Optional[float] = None, work_allowance_higher: Optional[float] = None, work_allowance_lower: Optional[float] = None, child_limit: Optional[int] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `WealthTaxParams` — ModelMetaclass

```python
WealthTaxParams(*, enabled: Optional[bool] = None, threshold: Optional[float] = None, rate: Optional[float] = None) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `WinnersLosers` — ModelMetaclass

```python
WinnersLosers(*, winners_pct: float, losers_pct: float, unchanged_pct: float, avg_gain: float, avg_loss: float) -> None
```

!!! abstract "Usage Documentation"
    [Models](../concepts/models.md)

A base class for creating Pydantic models.

Attributes:
    __class_vars__: The names of the class variables defined on the model.
    __private_attributes__: Metadata about the private attributes of the model.
    __signature__: The synthesized `__init__` [`Signature`][inspect.Signature] of the model.

    __pydantic_complete__: Whether model building is completed, or if there are still undefined fields.
    __pydantic_core_schema__: The core schema of the model.
    __pydantic_custom_init__: Whether the model has a custom `__init__` function.
    __pydantic_decorators__: Metadata containing the decorators defined on the model.
        This replaces `Model.__validators__` and `Model.__root_validators__` from Pydantic V1.
    __pydantic_generic_metadata__: A dictionary containing metadata about generic Pydantic models.
        The `origin` and `args` items map to the [`__origin__`][genericalias.__origin__]
        and [`__args__`][genericalias.__args__] attributes of [generic aliases][types-genericalias],
        and the `parameter` item maps to the `__parameter__` attribute of generic classes.
    __pydantic_parent_namespace__: Parent namespace of the model, used for automatic rebuilding of models.
    __pydantic_post_init__: The name of the post-init method for the model, if defined.
    __pydantic_root_model__: Whether the model is a [`RootModel`][pydantic.root_model.RootModel].
    __pydantic_serializer__: The `pydantic-core` `SchemaSerializer` used to dump instances of the model.
    __pydantic_validator__: The `pydantic-core` `SchemaValidator` used to validate instances of the model.

    __pydantic_fields__: A dictionary of field names and their corresponding [`FieldInfo`][pydantic.fields.FieldInfo] objects.
    __pydantic_computed_fields__: A dictionary of computed field names and their corresponding [`ComputedFieldInfo`][pydantic.fields.ComputedFieldInfo] objects.

    __pydantic_extra__: A dictionary containing extra values, if [`extra`][pydantic.config.ConfigDict.extra]
        is set to `'allow'`.
    __pydantic_fields_set__: The names of fields explicitly set during instantiation.
    __pydantic_private__: Values of private attributes set on the model instance.

### `aggregate_microdata` — function

```python
aggregate_microdata(persons: "'pd.DataFrame'", benunits: "'pd.DataFrame'", households: "'pd.DataFrame'", year: 'int') -> "'SimulationResult'"
```

Aggregate post-simulation microdata DataFrames into a SimulationResult.

This mirrors the aggregation logic in src/main.rs but runs in Python,
allowing post-hooks to modify result columns before the final roll-up.

Deciles and winners/losers are based on reform_net_income (equivalised by
equivalisation_factor where available).  This approximates the Rust engine's
use of extended_net_income; the difference only matters for VAT/stamp duty/
wealth-tax reforms, which are unlikely to be applied as post-hooks.

### `capabilities` — function

```python
capabilities() -> 'dict'
```

Return a structured description of engine capabilities for LLM consumption.

Does not require authentication — reports only what is locally cached
plus static knowledge about the engine. Returns a plain dict suitable
for JSON serialisation.

### `combine_microdata` — function

```python
combine_microdata(baseline: "'MicrodataResult'", reform: "'MicrodataResult'") -> "'MicrodataResult'"
```

Combine baseline-run and reform-run microdata into one comparison view.

Baseline columns come from the original run. Reform columns come from the
structurally/policy-modified run. Unprefixed columns come from the reform run.

### `ensure_dataset` — function

```python
ensure_dataset(dataset: 'str', year: 'int') -> 'str'
```

Return a path to a dataset base dir, downloading the needed year if missing.

Supports: frs, lcfs, spi, was.

## Parameters JSON schema

Full schema of the `Parameters` pydantic model. Use this to construct reform overlays via `Parameters.model_validate({...})`.

```json
{
  "$defs": {
    "BenefitCapParams": {
      "properties": {
        "single_london": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Single London"
        },
        "single_outside_london": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Single Outside London"
        },
        "non_single_london": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Non Single London"
        },
        "non_single_outside_london": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Non Single Outside London"
        },
        "earnings_exemption_threshold": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Earnings Exemption Threshold"
        }
      },
      "title": "BenefitCapParams",
      "type": "object"
    },
    "CapitalGainsTaxParams": {
      "properties": {
        "annual_exempt_amount": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Annual Exempt Amount"
        },
        "basic_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Basic Rate"
        },
        "higher_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Higher Rate"
        }
      },
      "title": "CapitalGainsTaxParams",
      "type": "object"
    },
    "ChildBenefitParams": {
      "properties": {
        "eldest_weekly": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Eldest Weekly"
        },
        "additional_weekly": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Additional Weekly"
        },
        "hicbc_threshold": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Hicbc Threshold"
        },
        "hicbc_taper_end": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Hicbc Taper End"
        }
      },
      "title": "ChildBenefitParams",
      "type": "object"
    },
    "DisabilityPremiumParams": {
      "properties": {
        "disability_premium_single": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Disability Premium Single"
        },
        "disability_premium_couple": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Disability Premium Couple"
        },
        "enhanced_disability_premium_single": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Enhanced Disability Premium Single"
        },
        "enhanced_disability_premium_couple": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Enhanced Disability Premium Couple"
        },
        "severe_disability_premium": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Severe Disability Premium"
        },
        "carer_premium": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Carer Premium"
        }
      },
      "title": "DisabilityPremiumParams",
      "type": "object"
    },
    "HousingBenefitParams": {
      "properties": {
        "withdrawal_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Withdrawal Rate"
        },
        "personal_allowance_single_under25": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Personal Allowance Single Under25"
        },
        "personal_allowance_single_25_plus": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Personal Allowance Single 25 Plus"
        },
        "personal_allowance_couple": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Personal Allowance Couple"
        },
        "child_allowance": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Child Allowance"
        },
        "family_premium": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Family Premium"
        }
      },
      "title": "HousingBenefitParams",
      "type": "object"
    },
    "IncomeRelatedBenefitParams": {
      "properties": {
        "esa_allowance_single_under25": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Esa Allowance Single Under25"
        },
        "esa_allowance_single_25_plus": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Esa Allowance Single 25 Plus"
        },
        "esa_allowance_couple": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Esa Allowance Couple"
        },
        "esa_wrag_component": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Esa Wrag Component"
        },
        "esa_support_component": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Esa Support Component"
        },
        "jsa_allowance_single_under25": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Jsa Allowance Single Under25"
        },
        "jsa_allowance_single_25_plus": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Jsa Allowance Single 25 Plus"
        },
        "jsa_allowance_couple": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Jsa Allowance Couple"
        },
        "carers_allowance_weekly": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Carers Allowance Weekly"
        },
        "ca_earnings_disregard_weekly": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Ca Earnings Disregard Weekly"
        },
        "ca_min_hours_caring": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Ca Min Hours Caring"
        },
        "ca_care_recipient_min_age": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Ca Care Recipient Min Age"
        }
      },
      "title": "IncomeRelatedBenefitParams",
      "type": "object"
    },
    "IncomeTaxParams": {
      "properties": {
        "personal_allowance": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Personal Allowance"
        },
        "pa_taper_threshold": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Pa Taper Threshold"
        },
        "pa_taper_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Pa Taper Rate"
        },
        "uk_brackets": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/TaxBracket"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Uk Brackets"
        },
        "scottish_brackets": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/TaxBracket"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Scottish Brackets"
        },
        "dividend_allowance": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Dividend Allowance"
        },
        "dividend_basic_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Dividend Basic Rate"
        },
        "dividend_higher_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Dividend Higher Rate"
        },
        "dividend_additional_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Dividend Additional Rate"
        },
        "savings_starter_rate_band": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Savings Starter Rate Band"
        },
        "marriage_allowance_max_fraction": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Marriage Allowance Max Fraction"
        },
        "marriage_allowance_rounding": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Marriage Allowance Rounding"
        }
      },
      "title": "IncomeTaxParams",
      "type": "object"
    },
    "LabourSupplyParams": {
      "description": "OBR labour supply elasticities (Slutsky decomposition).\n\nSource: OBR (2023) \"Costing a cut in National Insurance contributions:\nthe impact on labour supply\"\nhttps://obr.uk/docs/dlm_uploads/NICS-Cut-Impact-on-Labour-Supply-Note.pdf\n\nSet `enabled=False` to suppress labour supply responses. All elasticity\nfields are optional; omitted fields retain OBR defaults.",
      "properties": {
        "enabled": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Enabled"
        },
        "subst_married_women_no_children": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subst Married Women No Children"
        },
        "subst_married_women_child_0_2": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subst Married Women Child 0 2"
        },
        "subst_married_women_child_3_4": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subst Married Women Child 3 4"
        },
        "subst_married_women_child_5_10": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subst Married Women Child 5 10"
        },
        "subst_married_women_child_11_plus": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subst Married Women Child 11 Plus"
        },
        "subst_lone_parents_child_0_4": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subst Lone Parents Child 0 4"
        },
        "subst_lone_parents_child_5_10": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subst Lone Parents Child 5 10"
        },
        "subst_lone_parents_child_11_18": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subst Lone Parents Child 11 18"
        },
        "subst_men_and_single_women": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subst Men And Single Women"
        },
        "income_married_women_no_children": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Married Women No Children"
        },
        "income_married_women_child_0_2": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Married Women Child 0 2"
        },
        "income_married_women_child_3_4": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Married Women Child 3 4"
        },
        "income_married_women_child_5_10": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Married Women Child 5 10"
        },
        "income_married_women_child_11_plus": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Married Women Child 11 Plus"
        },
        "income_lone_parents_child_0_4": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Lone Parents Child 0 4"
        },
        "income_lone_parents_child_5_10": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Lone Parents Child 5 10"
        },
        "income_lone_parents_child_11_18": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Lone Parents Child 11 18"
        },
        "income_men_and_single_women": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Men And Single Women"
        }
      },
      "title": "LabourSupplyParams",
      "type": "object"
    },
    "NationalInsuranceParams": {
      "properties": {
        "primary_threshold_annual": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Primary Threshold Annual"
        },
        "upper_earnings_limit_annual": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Upper Earnings Limit Annual"
        },
        "main_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Main Rate"
        },
        "additional_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Additional Rate"
        },
        "secondary_threshold_annual": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Secondary Threshold Annual"
        },
        "employer_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Employer Rate"
        },
        "class2_flat_rate_weekly": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Class2 Flat Rate Weekly"
        },
        "class2_small_profits_threshold": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Class2 Small Profits Threshold"
        },
        "class4_lower_profits_limit": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Class4 Lower Profits Limit"
        },
        "class4_upper_profits_limit": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Class4 Upper Profits Limit"
        },
        "class4_main_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Class4 Main Rate"
        },
        "class4_additional_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Class4 Additional Rate"
        }
      },
      "title": "NationalInsuranceParams",
      "type": "object"
    },
    "PensionCreditParams": {
      "properties": {
        "standard_minimum_single": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Standard Minimum Single"
        },
        "standard_minimum_couple": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Standard Minimum Couple"
        },
        "savings_credit_threshold_single": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Savings Credit Threshold Single"
        },
        "savings_credit_threshold_couple": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Savings Credit Threshold Couple"
        }
      },
      "title": "PensionCreditParams",
      "type": "object"
    },
    "ScottishChildPaymentParams": {
      "properties": {
        "weekly_amount": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Weekly Amount"
        },
        "max_age": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Max Age"
        }
      },
      "title": "ScottishChildPaymentParams",
      "type": "object"
    },
    "StampDutyBand": {
      "properties": {
        "rate": {
          "title": "Rate",
          "type": "number"
        },
        "threshold": {
          "title": "Threshold",
          "type": "number"
        }
      },
      "required": [
        "rate",
        "threshold"
      ],
      "title": "StampDutyBand",
      "type": "object"
    },
    "StampDutyParams": {
      "properties": {
        "bands": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/StampDutyBand"
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Bands"
        },
        "annual_purchase_probability": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Annual Purchase Probability"
        }
      },
      "title": "StampDutyParams",
      "type": "object"
    },
    "StatePensionParams": {
      "properties": {
        "new_state_pension_weekly": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "New State Pension Weekly"
        },
        "old_basic_pension_weekly": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Old Basic Pension Weekly"
        }
      },
      "title": "StatePensionParams",
      "type": "object"
    },
    "TaxBracket": {
      "properties": {
        "rate": {
          "title": "Rate",
          "type": "number"
        },
        "threshold": {
          "title": "Threshold",
          "type": "number"
        }
      },
      "required": [
        "rate",
        "threshold"
      ],
      "title": "TaxBracket",
      "type": "object"
    },
    "TaxCreditsParams": {
      "properties": {
        "wtc_basic_element": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Wtc Basic Element"
        },
        "wtc_couple_element": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Wtc Couple Element"
        },
        "wtc_lone_parent_element": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Wtc Lone Parent Element"
        },
        "wtc_30_hour_element": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Wtc 30 Hour Element"
        },
        "ctc_child_element": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Ctc Child Element"
        },
        "ctc_family_element": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Ctc Family Element"
        },
        "ctc_disabled_child_element": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Ctc Disabled Child Element"
        },
        "ctc_severely_disabled_child_element": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Ctc Severely Disabled Child Element"
        },
        "income_threshold": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Threshold"
        },
        "taper_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Taper Rate"
        },
        "wtc_min_hours_single": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Wtc Min Hours Single"
        },
        "wtc_min_hours_couple": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Wtc Min Hours Couple"
        }
      },
      "title": "TaxCreditsParams",
      "type": "object"
    },
    "UcMigrationRates": {
      "properties": {
        "housing_benefit": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Housing Benefit"
        },
        "tax_credits": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Tax Credits"
        },
        "income_support": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Income Support"
        }
      },
      "title": "UcMigrationRates",
      "type": "object"
    },
    "UniversalCreditParams": {
      "properties": {
        "standard_allowance_single_under25": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Standard Allowance Single Under25"
        },
        "standard_allowance_single_over25": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Standard Allowance Single Over25"
        },
        "standard_allowance_couple_under25": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Standard Allowance Couple Under25"
        },
        "standard_allowance_couple_over25": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Standard Allowance Couple Over25"
        },
        "child_element_first": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Child Element First"
        },
        "child_element_subsequent": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Child Element Subsequent"
        },
        "disabled_child_lower": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Disabled Child Lower"
        },
        "disabled_child_higher": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Disabled Child Higher"
        },
        "lcwra_element": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Lcwra Element"
        },
        "carer_element": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Carer Element"
        },
        "taper_rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Taper Rate"
        },
        "work_allowance_higher": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Work Allowance Higher"
        },
        "work_allowance_lower": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Work Allowance Lower"
        },
        "child_limit": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Child Limit"
        }
      },
      "title": "UniversalCreditParams",
      "type": "object"
    },
    "WealthTaxParams": {
      "properties": {
        "enabled": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Enabled"
        },
        "threshold": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Threshold"
        },
        "rate": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Rate"
        }
      },
      "title": "WealthTaxParams",
      "type": "object"
    }
  },
  "description": "Full parameter set. All fields optional for use as reform overlay.",
  "properties": {
    "fiscal_year": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Fiscal Year"
    },
    "income_tax": {
      "anyOf": [
        {
          "$ref": "#/$defs/IncomeTaxParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "national_insurance": {
      "anyOf": [
        {
          "$ref": "#/$defs/NationalInsuranceParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "universal_credit": {
      "anyOf": [
        {
          "$ref": "#/$defs/UniversalCreditParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "child_benefit": {
      "anyOf": [
        {
          "$ref": "#/$defs/ChildBenefitParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "state_pension": {
      "anyOf": [
        {
          "$ref": "#/$defs/StatePensionParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "pension_credit": {
      "anyOf": [
        {
          "$ref": "#/$defs/PensionCreditParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "benefit_cap": {
      "anyOf": [
        {
          "$ref": "#/$defs/BenefitCapParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "housing_benefit": {
      "anyOf": [
        {
          "$ref": "#/$defs/HousingBenefitParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "tax_credits": {
      "anyOf": [
        {
          "$ref": "#/$defs/TaxCreditsParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "scottish_child_payment": {
      "anyOf": [
        {
          "$ref": "#/$defs/ScottishChildPaymentParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "uc_migration": {
      "anyOf": [
        {
          "$ref": "#/$defs/UcMigrationRates"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "disability_premiums": {
      "anyOf": [
        {
          "$ref": "#/$defs/DisabilityPremiumParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "income_related_benefits": {
      "anyOf": [
        {
          "$ref": "#/$defs/IncomeRelatedBenefitParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "capital_gains_tax": {
      "anyOf": [
        {
          "$ref": "#/$defs/CapitalGainsTaxParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "stamp_duty": {
      "anyOf": [
        {
          "$ref": "#/$defs/StampDutyParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "wealth_tax": {
      "anyOf": [
        {
          "$ref": "#/$defs/WealthTaxParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "labour_supply": {
      "anyOf": [
        {
          "$ref": "#/$defs/LabourSupplyParams"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    }
  },
  "title": "Parameters",
  "type": "object"
}
```
