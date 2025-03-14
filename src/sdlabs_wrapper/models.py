import json
import logging
import os
from dataclasses import InitVar, asdict, dataclass, field
from enum import Enum
from math import floor, log10
from typing import Dict, List

import scientia_sdk as sct
from dataclasses_jsonschema import JsonSchemaMixin, SchemaType

LOGGER = logging.getLogger(__name__)


class AccountType(Enum):
    enterprise = "enterprise"
    academic = "academic"


class Environment(Enum):
    com = "com"
    network = "network"
    dev = "dev"


class Optimizer(Enum):
    falcondngo = "falcondngo"
    falcongpbo = "falcongpbo"
    edboplus = "edboplus"
    grid = "grid"
    randomsearch = "randomsearch"
    semopt = "semopt"


class ObjectiveGoal(Enum):
    min = "min"
    max = "max"
    target = "target"


class MofOption(Enum):
    chimera = "chimera"
    weighted_sum = "weighted_sum"


@dataclass
class DescriptorProperty(JsonSchemaMixin):
    """Single property of category."""

    key: str
    value: float


@dataclass
class Descriptor(JsonSchemaMixin):
    """Categorical parameter (contains category and its descriptor).

    If properties are provided, they should be present for all
    categorical options
    """

    category: str
    properties: List[DescriptorProperty] = None

    def __post_init__(self):
        if self.properties and isinstance(self.properties[0], dict):
            self.properties = [DescriptorProperty(**prop) for prop in self.properties]

    def to_dict(self):
        """Return all key-value pairs that are not None."""
        res = asdict(self)
        return {key: val for key, val in res.items() if val is not None}


@dataclass
class Parameter(JsonSchemaMixin):
    """Single parameter object (can be discrete, categorical or continuous)"""

    name: str = None
    type: str = field(
        default="continuous",
        metadata={
            "validator": lambda x: x in ["continuous", "discrete", "categorical"]
        },
    )  # or 'discrete' or 'categorical'
    high_value: float = None
    low_value: float = None
    stride: float = None
    description: str = None
    descriptors: List[Descriptor] = None

    def __post_init__(self):
        if self.descriptors and isinstance(self.descriptors[0], dict):
            self.descriptors = [Descriptor(**descr) for descr in self.descriptors]

    @property
    def _base_10_exponent(self):
        if self.type == "categorical":
            return None
        exp_val = self.find_exp(self.low_value or self.stride or self.high_value)
        return abs(exp_val) if exp_val < -4 else None

    def rescale_units_to_sdlabs(self, val):
        """Rescale to SDLabs format (transform really small values using their
        base_10_exponent)"""
        if self.type == "categorical":
            return val
        if not self._base_10_exponent:
            return val
        return round(val * 10**self._base_10_exponent, 2)

    def rescale_units_to_user(self, val):
        """Rescale with respect to exponent (convert from microns to m for
        example)"""
        if self.type == "categorical":
            return val
        val = float(val)
        if not self._base_10_exponent:
            return val
        val /= 10**self._base_10_exponent
        return round(val, self._base_10_exponent + 2)

    def find_exp(self, number) -> int:
        base10 = log10(abs(number))
        return floor(base10)

    def format_sdlabs_description(self) -> str:

        if self._base_10_exponent:
            return (
                self.description or ""
            ) + f" in base units * 10^(-{self._base_10_exponent})"
        return self.description

    def to_dict(self):
        """Return all key-value pairs that are not None."""
        res = asdict(self)
        if self.descriptors:
            res["descriptors"] = [descr.to_dict() for descr in self.descriptors]
        return {key: val for key, val in res.items() if val is not None}


@dataclass
class ConstraintDefinition(JsonSchemaMixin):
    parameter: str = field(metadata={"description": "Name of the parameter"})
    bounds: List[List[any]] = field(
        default=None,
        metadata={
            "description": "Bounds of the parameter as a list of lists. Each list should have up to 2 elements"
        },
    )
    weight: float = field(
        default=None,
        metadata={"description": "Weight of the parameter in the constraint"},
    )

    def __post_init__(self):
        # cast all bounds to strings
        if self.bounds:
            self.bounds = [
                [str(bound) for bound in bound_list] for bound_list in self.bounds
            ]


@dataclass
class Constraint(JsonSchemaMixin):
    definitions: List[ConstraintDefinition] = None
    targets: List[float] = field(
        default=None, metadata={"description": "Target values for the constraints"}
    )
    name: str = field(default=None, metadata={"description": "Name of the constraint"})
    type: str = field(
        default="linear_eq",
        metadata={
            "validator": lambda x: x
            in [
                "exclusion",
                "conditional_exclusion",
                "linear_eq",
                "linear_lte",
                "linear_gte",
                "linear_between",
            ]
        },
    )

    def __post_init__(self):
        if self.definitions and isinstance(self.definitions[0], dict):
            self.definitions = [
                ConstraintDefinition(**cstr) for cstr in self.definitions
            ]
        if not self.name:
            self.name = self.type


@dataclass
class MultiObjectiveConfiguration(JsonSchemaMixin):
    """MultiObjectiveConfiguration. It can be either `chimera` or
    `weighted_sum`.

    Chimera is a hierarchical approach to optimize the process.
    The hierarchy value should be a unique positive integer starting at 0, where 0 is the most important.
    The tolerance specifies how much a user is willing to sacrifice of this objective to improve the subsequent one.
    It can be specified in absolute or relative terms. If `relative` tolerance is specified, then it should be stated as a percentage value ranging from 0-100
    Else, if `absolute` is selected, it should be a positive value that matches the range of the objective. Either `absolute` or `relative` should be chosen.

    Weighted sum requires a `weight` value to be provided. The weight and the values will be renormalized with respect to the weights of other objectives (if any)
    """

    hierarchy: int = field(
        default=0,
        metadata={
            "description": "Objective importance. The lower the better. Should be larger or equal to 0. Should be unique",
            "minimum": 0,
        },
    )
    relative: float = field(
        default=None,
        metadata={
            "description": "Relative tolerance to sacrifice from current objective (relative to best found). Should be a percentage (from 0 to 100). If this is the last objective, set it to 0.",
            "minimum": 0,
            "maximum": 100,
        },
    )
    absolute: float = field(
        default=None,
        metadata={
            "description": "Absolute tolerance to sacrifice from current objective. If this is the last objective, set it to 0.",
            "minimum": 0,
        },
    )  # absolute tolerance to sacrifice from current objective to gain on the next
    weight: float = field(
        default=None,
        metadata={
            "description": "Relative weight associated to this objective. The value is normalized with respect to the other provided weights. The objectives will also be min-max normalized before applying the weights",
            "minimum": 0,
        },
    )  # weight used for weighted_sum

    def __post_init__(self):
        pass

    def to_dict(self):
        return asdict(self)


@dataclass
class Objective(JsonSchemaMixin):
    name: str
    goal: ObjectiveGoal = ObjectiveGoal.max
    multi_objective_configuration: MultiObjectiveConfiguration = (
        None  # chimera or weighted sum options
    )
    _id: str = None  # internal to SDLabs
    target: float = None

    def __post_init__(self):
        if isinstance(self.multi_objective_configuration, dict):
            self.multi_objective_configuration = MultiObjectiveConfiguration(
                **self.multi_objective_configuration
            )

    def to_dict(self):
        return asdict(self)


@dataclass
class OptimizationConfig(JsonSchemaMixin):
    """Full configuration specifications required to start the optimization.

    It lists a set of parameters, and a set of objectives. If More than
    one objective is provided
    """

    parameters: List[Parameter] = None
    objectives: List[Objective] = None
    optimization_name: str = "SampleOptimization"

    description: str = None

    api_key: str = field(
        default=None,
        metadata={
            "description": "SDLabs API key. If not found, it will be fetched from `SDLABS_API_KEY` environment variable."
        },
    )
    sdlabs_group_id: str = "Atinary"
    sdlabs_account_type: AccountType = field(
        default=AccountType.academic,
        metadata={
            "description": """Account type will determine the endpoint of the API calls.
                                                                                    If Academic, certain functionalities will not be supported.
                                                                                    For more information please refer to the terms and service of SDLabs"""
        },
    )
    sdlabs_environment: Environment = field(
        default=Environment.com,
        metadata={
            "description": """Environment will determine the endpoint of the API calls."""
        },
    )
    constraints: List[Constraint] = field(default=None)
    multi_objective_function: MofOption = field(default=None)
    algorithm: Optimizer = field(
        default=Optimizer.edboplus,
        metadata={
            "description": "Optimization algorithm to be chosen. See more on Atinary documentation",
        },
    )
    batch_size: int = field(
        default=1,
        metadata={
            "description": "Number of  recommendations per iteration. Should be larger than 1 and less than 20"
        },
    )
    spec_file_path: InitVar[str] = field(
        default=None,
        metadata={
            "description": "Specification file path. The file should have the same schema as this configuration object"
        },
    )
    input_content: InitVar[Dict[str, any]] = field(
        default=None, metadata={"description": "Contents as a dictionary"}
    )
    budget: int = field(
        default=20,
        metadata={
            "description": "Total number of iterations to carry out in the optimization"
        },
    )
    random_seed: int = 2022
    # whether a new campaign should be restarted every time
    always_restart: bool = field(
        default=False,
        metadata={
            "description": "Whether every time that we launch `initialize_optimization` a new optimization will be restarted. If False then any ongoing optimizations will be resumed"
        },
    )
    # whether we want to inherit data from the template when we launch the new campaign
    inherit_data: bool = field(
        default=False,
        metadata={
            "description": "Whether the current optimization will reuse previous optimizations with the same name (see more on home.atinary.com)"
        },
    )

    @property
    def _account_type_url_str(self):
        if self.sdlabs_account_type == AccountType.enterprise:
            return "enterprise"
        return "scientia"

    @property
    def sdlabs_endpoint_url(self):
        return f"https://api.{self._account_type_url_str}.atinary.{self.sdlabs_environment.name}/sdlabs/latest"

    def __post_init__(
        self, spec_file_path: str = None, input_content: Dict[str, any] = None
    ):
        json_obj = None
        if input_content:
            json_obj = input_content
        elif spec_file_path:
            with open(spec_file_path, "rb") as spec_file:
                json_obj = json.load(spec_file)
        if json_obj:
            self.__init__(**{"api_key": self.api_key, **json_obj})
            return
        if self.parameters and isinstance(self.parameters[0], dict):
            self.parameters = [Parameter(**prm) for prm in self.parameters]
        if self.objectives and isinstance(self.objectives[0], dict):
            self.objectives = [Objective(**obj) for obj in self.objectives]
        if self.constraints and isinstance(self.constraints[0], dict):
            self.constraints = [Constraint(**cstr) for cstr in self.constraints]
        if not self.api_key:
            self.api_key = os.environ.get("SDLABS_API_KEY")
        if isinstance(self.sdlabs_account_type, str):
            self.sdlabs_account_type = AccountType[self.sdlabs_account_type]
        if isinstance(self.sdlabs_environment, str):
            self.sdlabs_environment = Environment[self.sdlabs_environment]


@dataclass
class Recommendation(JsonSchemaMixin):
    """Recommendation from SDLabs.

    The parameter values are present in the `param_values` attribute.
    `param_values` is a dictionary with keys matching the parameter
    names provided in the OptimizationConfig `measurements` is a
    dictionary with keys matches the objective names provided in the
    OptimizationConfig
    """

    _obs_obj: sct.LatestObservationsObj = None
    _parameter_map: Dict[str, Parameter] = field(
        default_factory=dict,
        metadata={
            "description": "Dictionary with key name and value Parameter. Only required if _obs_obj is not None"
        },
    )
    param_values: Dict[str, any] = field(
        default_factory=dict,
        metadata={
            "description": "Dictionary with key name and value float or string (if categorical)"
        },
    )
    measurements: Dict[str, float] = field(
        default_factory=dict,
        metadata={"description": "Dictionary with key name and value float"},
    )

    def __post_init__(self):
        if self._obs_obj:
            self.param_values = {}
            for prm_val_obj in self._obs_obj.parameters:
                param = self._parameter_map.get(prm_val_obj.name)
                if param.type == "categorical":
                    self.param_values[prm_val_obj.name] = str(prm_val_obj.value)
                else:
                    self.param_values[prm_val_obj.name] = param.rescale_units_to_user(
                        float(prm_val_obj.value)
                    )
            self.measurements = {
                msr_val_obj.name: float(msr_val_obj.value)
                if msr_val_obj.value
                else None
                for msr_val_obj in self._obs_obj.measurements
            }

    @property
    def latest_observation_obj(self) -> sct.LatestObservationsObj:
        for prm_val_obj in self._obs_obj.parameters:
            param = self._parameter_map.get(prm_val_obj.name)
            prm_val_obj.value = str(
                param.rescale_units_to_sdlabs(self.param_values[prm_val_obj.name])
            )
        for msr_val_obj in self._obs_obj.measurements:
            msr_val_obj.value = str(self.measurements[msr_val_obj.name])
        return self._obs_obj

    @property
    def iteration(self):
        return self._obs_obj.iteration

    @property
    def batch(self):
        return self._obs_obj.batch_number


def generate_and_write_schema(file_path="sdlabs_wrapper_schema.json"):
    with open("sdlabs_wrapper_schema.json", "w") as f:
        schema = JsonSchemaMixin.all_json_schemas(schema_type=SchemaType.SWAGGER_V3)
        json.dump(schema, f, indent=2)
    LOGGER.info(f"Successfully created schema at {file_path}")


if __name__ == "__main__":
    generate_and_write_schema()
