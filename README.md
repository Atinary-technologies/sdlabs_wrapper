# SDLabs Wrapper
This wrapper is used for quick usage of SDLabs for optimization purposes.
## Initialize
1. Install the sdlabs_sdk (follow tutorial on SDLabs platform)
2. `pip install .` in the root directory
4. Fetch your API key from the Account page on SDLabs platform and export it as an env variable:
   `export SDLABS_API_KEY=<paste it here>`
## Quickstart
Create optimization specs inside of `config/optimization_config.json` (see [JSON schema #/components/schemas/OptimizationConfig](./sdlabs_wrapper_schema.json#/components/schemas/OptimizationConfig))
```
design_json = {
    "optimization_name": "Example",
    "description": "Optimize example",
    "sdlabs_group_id": "Atinary",
    "parameters": [
        {
            "name": "param_a",
            "low_value": 0,
            "high_value": 10,
            "type": "continuous"
        },
        {
            "name": "param_b",
            "low_value": 0,
            "high_value": 10,
            "type": "continuous"
        }
    ],
    "multi_objective_function": "chimera",
    "objectives": [
        {
            "name": "conductivity",
            "goal": "maximize",
            "multi_objective_configuration": {
                "hierarchy": 0,
                "relative": 10
            }
        },
        {
            "name": "toxicity",
            "goal": "min",
            "multi_objective_configuration": {
                "hierarchy": 1,
                "relative": 10
            }
        }
    ],
    "batch_size": 3,
    "algorithm": "dragonfly",
    "budget": 20
}

```
Initialize optimization (with api key). Either pass it as an argument or export it as an env variable  `SDLABS_API_KEY`
```
import random
from sdlabs_wrapper.wrapper import initialize_optimization
opt_wrapper = initialize_optimization(spec_file_content=design_json)
for iteration in range(opt_wrapper.config.budget):
    suggestions = opt_wrapper.get_new_suggestions(max_retries=4, sleep_time_s=15)
    LOGGER.info(f"Iteration {iteration+1} New Suggestions: {suggestions}")
    for suggestion in suggestions:
        suggestion.measurements = {
            "toxicity": random.random(),
            "conductivity": random.random(),
        }
    if suggestions:
        opt_wrapper.send_measurements(suggestions)
```
## Examples
* [Optimize Battmo simulation](./examples/battmo_optimization/optimize_battmo_simulation.ipynb)
## Constraints (Only supported with premium algorithms)
You can see the format of constraints inside of `atinary_wrapper/models.py::Constraint`.
### Linear Equality Constraints
Here is an example of a LinearEquality constraint where we want the sum of Manganese, Iron, Chromium and Aluminum to add up to 1.0.
``` json
"constraints": [
        {
            "type": "linear_eq",
            "definitions": [
                {
                    "parameter": "Manganese",
                    "weight": 1
                },
                {
                    "parameter": "Iron",
                    "weight": 1
                },
                {
                    "parameter": "Chromium",
                    "weight": 1
                },
                {
                    "parameter": "Aluminum",
                    "weight": 1
                }
            ],
            "targets": [
                1
            ]
        }
]
```
### Exclusion constraint
This one only excludes a specific range of a single parameter (Manganese cannot be between 0 and 1).
``` json
"constraints": [

        {
            "type":"exclusion",
            "definitions":[
                {"parameter":"Manganese","bounds":[[0,1]]}
            ]
        }
    ]
```
### Conditional Exclusion constraint
This one only excludes a conditional range of 2 or more parameters (Manganese cannot have a value between 0 and 1 if Iron is between 0 and 2 and viceversa)
``` json
"constraints": [

        {
            "type":"conditional_exclusion",
            "definitions":[
                {"parameter":"Manganese","bounds":[[0,1]]},
                {"parameter":"Iron","bounds":[[0,2]]}
            ]
        }
    ]
```
### More info
You can inspect the `atinary_wrapper/models.py` for more information about the data models.
