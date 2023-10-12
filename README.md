# SDLabs Wrapper
This wrapper is used for quick usage of SDLabs for optimization purposes.
## Initialize
1. Install the sdlabs_sdk (follow tutorial on SDLabs platform)
2. `pip install .` in the root directory
4. Fetch your API key from the Account page on SDLabs platform and export it as an env variable:
   `export SDLABS_API_KEY=<paste it here>`
## Quickstart
<!-- open in colab link-->
<a href="https://colab.research.google.com/drive/1BL6CxQgIuosys-7ROztNifXNk4M50mvR?usp=sharing" target="_blank">
  <img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/>
</a>

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
            "goal": "max",
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
import json
import random

file_path = "config/optimization_config.json"
# load config as dict
with open(file_path, "rb") as f:
    config_dict = json.load(f)
wrapper = initialize_optimization(
    spec_file_content=config_dict,
)
for iteration in range(wrapper.config.budget):
    LOGGER.info(f"Iteration {iteration+1}: Fetching new suggestions")
    suggestions = wrapper.get_new_suggestions(max_retries=6, sleep_time_s=30)
    LOGGER.info(f"Iteration {iteration+1} New Suggestions: {suggestions}")
    for suggestion in suggestions:
        for obj in wrapper.config.objectives:
            suggestion.measurements[obj.name] = random.random()
    if suggestions:
        wrapper.send_measurements(suggestions)
        LOGGER.info(f"Iteration {iteration+1} Measurements sent")
```
## Examples
* [Optimize Battmo simulation](./examples/battmo_optimization/optimize_battmo_simulation.ipynb)
## Constraints (Only supported with enterprise account and premium algorithms)
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

## Funding
This project has received funding from the European Union’s Horizon 2020 research and innovation programme under grant agreement No 957189. The project is part of BATTERY 2030+, the large-scale European research initiative for inventing the sustainable batteries of the future.
