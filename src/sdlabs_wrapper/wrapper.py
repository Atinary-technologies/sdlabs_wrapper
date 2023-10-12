import logging
import time
from dataclasses import dataclass
from typing import Dict, List

import scientia_sdk as sct

import sdlabs_wrapper.config as config
import sdlabs_wrapper.models as models

LOGGER = logging.getLogger(__name__)
NEXUS_ENDPOINT_URL = "https://scientia.atinary.com/nexus/api/latest/"


@dataclass
class SDLabsWrapper:
    config: models.OptimizationConfig = None
    _campaign_id: str = None  # campaign id
    _sdlabs_api_client: sct.ApiClient = None  # api client of sdlabs sdk
    _workstation: sct.WorkstationObj = None  # workstation
    _current_parameter_file_ids: List[str] = None

    def __post_init__(self):
        self.config = self.config or config.init()
        configuration = sct.Configuration(
            host=self.config.sdlabs_endpoint_url,
            api_key={
                "api_key": self.config.api_key,
            },
        )
        configuration.access_token = None

        self.sdlabs_api_client = sct.ApiClient(configuration)

        self.wst_api = sct.WorkstationApi(self.sdlabs_api_client)
        self.prm_api = sct.ParameterApi(self.sdlabs_api_client)
        self.tpl_api = sct.TemplateApi(self.sdlabs_api_client)
        self.cpg_api = sct.CampaignApi(self.sdlabs_api_client)
        self.opt_api = sct.OptimizerApi(self.sdlabs_api_client)
        self._campaign_id = None

    def _start_optimization(self):

        # List workstations
        wsts = self.wst_api.workstations_list(
            is_public=False, group_id=self.config.sdlabs_group_id
        ).objects
        workstation = next(
            (wst for wst in wsts if wst.name == self.config.optimization_name), None
        )
        if workstation:
            self.workstation = self.wst_api.workstation_get(workstation.id).object
        else:
            LOGGER.info(
                f"Creating new workstation associated to parameters {self.config.parameters} and measurements {[obj.name for obj in self.config.objectives]}"
            )
            # Create some metadata
            wst_name = self.config.optimization_name
            wst_description = self.config.description
            wst_bandwidth = 99  # How many recommendations can be handled in parallel?
            # Define parameters
            params = []
            for prm in self.config.parameters:
                prm_dict = {
                    **prm.to_dict(),
                    "description": prm.format_sdlabs_description(),
                    "low_value": prm.rescale_units_to_sdlabs(prm.low_value),
                    "high_value": prm.rescale_units_to_sdlabs(prm.high_value),
                    "stride": prm.rescale_units_to_sdlabs(prm.stride)
                    if prm.stride
                    else None,
                }
                params.append(sct.ParameterObj(**prm_dict))
            wst_params = [
                self.prm_api.parameter_create(parameter_obj=prm).object
                for prm in params
            ]
            # create measurements
            wst_measurements = [obj.name for obj in self.config.objectives]
            # create workstation
            self.workstation = self.wst_api.workstation_create(
                workstation_obj=sct.WorkstationObj(
                    name=wst_name,
                    description=wst_description,
                    conn_type=sct.ConnectionType.API,
                    bandwidth=wst_bandwidth,
                    measurements=wst_measurements,
                    parameters=[p.id for p in wst_params],
                )
            ).object
        # check if template exists
        LOGGER.info(f"Linked to workstation '{self.workstation.id}'")
        tpls = self.tpl_api.templates_list(group_id=self.config.sdlabs_group_id).objects
        template_id = next(
            (
                tpl.id
                for tpl in tpls
                if tpl.name == f"{self.workstation.name} Optimization Template"
            ),
            None,
        )

        if template_id:
            # fetch template and update budget, batch-size and random seed
            # to do so, we need to pass full object
            self.template = self.tpl_api.template_get(template_id=template_id).object
            if self.config.budget != self.template.budget:
                # update budget of template
                self.template.budget = self.config.budget
                tpl_dict = self.template.to_dict()
                # replace constraints, objective, optimizer, multi-objective function
                # with ids
                for key in [
                    "constraints",
                    "parameters",
                    "objective",
                    "optimizer",
                    "multi_objective_function",
                ]:
                    # replace with id (required to update template)
                    if key == "parameters":
                        tpl_dict["parameters"] = [
                            sct.StepObj(
                                level=level_obj.level,
                                parameters=[
                                    sct.ParameterCpgObj(
                                        parameter_id=step_prm.parameter.id,  # Copy of the workstation's parameter / parameters names should match!
                                        workstation_id=step_prm.workstation.id,
                                    )
                                    for step_prm in level_obj.parameters
                                ],
                            )
                            for level_obj in self.template.parameters
                        ]
                    elif key == "constraints" and self.template.constraints:
                        tpl_dict[key] = [cstr.id for cstr in self.template.constraints]
                    elif key == "objective":
                        tpl_dict["objective"] = self.template.objectives[0].id
                    else:
                        tpl_dict[key] = getattr(
                            getattr(self.template, key, {}), "id", None
                        )
                tpl_obj = sct.TemplateObj(**tpl_dict)
                self.tpl_api.template_update(
                    template_id=template_id, template_obj=tpl_obj
                )
                LOGGER.debug("Updated template budget")
            # update batch-size and random seed (need optimizer)
            opt_object = sct.OptObj(**self.template.optimizer.to_dict())
            for conf in opt_object.configuration:
                # loop over configuration (list of dictionaries with the following format
                # [{"key":<key>, "value":<value>}]). Find key matching batch_size and random_seed
                # and update value
                if conf.key in ("batch_size", "random_seed"):
                    conf.value = str(getattr(self.config, conf.key))
            self.opt_api.optimizer_update(
                self.template.optimizer.id, opt_obj=opt_object
            )
            LOGGER.debug("Updated template batch size and random seed")
        else:
            # Create template

            obj_api = sct.ObjectiveApi(self.sdlabs_api_client)
            mof_api = sct.MultiObjectiveFunctionApi(self.sdlabs_api_client)
            # create parameters
            tpl_params = [
                self.prm_api.parameter_copy(
                    prm.id,
                    parameter_copy=sct.ParameterCopy(name=prm.name),
                ).object
                for prm in self.workstation.parameters
            ]
            # create optimizer
            opt_configuration = [
                sct.OptObjConfiguration(key=property, value=str(val))
                for property, val in [
                    ("batch_size", self.config.batch_size),
                    ("random_seed", self.config.random_seed),
                ]
            ]
            opt_id = self.opt_api.optimizer_create(
                opt_obj=sct.OptObj(
                    configuration=opt_configuration,
                    function=self.config.algorithm.lower(),
                    name=f"{self.config.sdlabs_group_id}-{self.config.algorithm}",
                )
            ).object.id
            # create objective
            obj_kwargs = {}
            objectives = [
                obj_api.objective_create(
                    objective_obj=sct.ObjectiveObj(
                        name=obj.name,  # should match workstation measurements
                        description=f'{obj.goal.title()} the {obj.name.replace("_"," ")}',
                        goal=obj.goal,  # can be 'min', 'max' or 'target'
                        # target=100,
                    )
                ).object
                for obj in self.config.objectives
            ]  # only take single objective

            if len(objectives) == 1:
                obj_kwargs["objective"] = objectives[0].id
            else:
                for obj in self.config.objectives:
                    # append id to config objectives
                    obj_id = next(
                        sdlabs_obj.id
                        for sdlabs_obj in objectives
                        if sdlabs_obj.name == obj.name
                    )
                    obj._id = obj_id
                obj_kwargs["multi_objective_function"] = mof_api.mof_create(
                    mof_obj=sct.MofObj(
                        function=self.config.multi_objective_function,
                        name=self.config.multi_objective_function,
                        configuration=[
                            sct.MofObjConfig(
                                objective_id=obj._id,
                                **obj.multi_objective_configuration.to_dict(),
                            )
                            for obj in self.config.objectives
                        ],
                    )
                ).object.id
            # constraints
            sdlabs_constraints = []
            if self.config.constraints:
                # map parameter names to ids of template parameters
                constraints_api = sct.ConstraintApi(self.sdlabs_api_client)
                sdlabs_constraints = []
                for constraint in self.config.constraints:
                    # replace parameter with id of template parameter
                    for cstr_defn in constraint.definitions:
                        if isinstance(cstr_defn, dict):
                            cstr_defn["parameter"] = next(
                                prm.id
                                for prm in tpl_params
                                if prm.name == cstr_defn["parameter"]
                            )
                        else:
                            cstr_defn.parameter = next(
                                prm.id
                                for prm in tpl_params
                                if prm.name == cstr_defn.parameter
                            )

                sdlabs_constraints = constraints_api.constraint_create_many(
                    constraint_obj=[
                        sct.ConstraintObj(**cstr.to_dict())
                        for cstr in self.config.constraints
                    ]
                ).objects

            self.template = self.tpl_api.template_create(
                template_obj=sct.TemplateObj(
                    # budget: total number of objective function measurements allowed
                    budget=self.config.budget,
                    # define optimizer
                    optimizer=opt_id,
                    # name of the optimization template
                    name=f"{self.workstation.name} Optimization Template",
                    # define the objective(s)
                    **obj_kwargs,
                    # define the parameters for each workstation
                    parameters=[
                        sct.StepObj(
                            level=1,
                            parameters=[
                                sct.ParameterCpgObj(
                                    parameter_id=prm.id,  # Copy of the workstation's parameter / parameters names should match!
                                    workstation_id=self.workstation.id,
                                )
                                for prm in tpl_params
                            ],
                        )
                    ],
                    constraints=[cstr.id for cstr in sdlabs_constraints],
                )
            ).object
        # Check if any campaigns are running. If so, take one of them
        states = self.cpg_api.campaigns_state(
            template_ids=[self.template.id], group_id=self.config.sdlabs_group_id
        ).objects
        for cpg_state in states:
            if cpg_state.state == "running":
                if not self.config.always_restart:
                    # continuing active campaign
                    LOGGER.info(
                        f"Continuing active campaign '{cpg_state.campaigns[0].id}'. No new campaign will be created"
                    )
                    self._campaign_id = cpg_state.campaigns[0].id
                else:
                    LOGGER.info(
                        f"`always_restart` flag was selected. Stopping all running campaigns and launching a new one. {[cpg.id for cpg in cpg_state.campaigns]}"
                    )
                    # stop all running campaigns associated to the template
                    for running_cpg in cpg_state.campaigns:
                        self.cpg_api.campaign_operation(
                            campaign_id=running_cpg.id,
                            campaign_operation=sct.CampaignOperation(operation="stop"),
                        )
                    time.sleep(2)

        if not self._campaign_id:

            self._campaign_id = self.tpl_api.template_run(
                self.template.id,
                template_run_obj=sct.TemplateRunObj(
                    preload_data=self.config.inherit_data
                ),
            ).object.id
            LOGGER.info(
                f"New campaign launched! Preload data set to '{self.config.inherit_data}'."
            )
        LOGGER.info(
            f"Running optimization associated with campaign id : '{self._campaign_id}' associated to template '{self.template.id}'"
        )

    def get_new_suggestions(
        self, max_retries=5, sleep_time_s=30
    ) -> List[sct.LatestObservationsObj]:
        """Get new parameters.

        Args:
            sdlabs_wrapper (SDLabsWrapper, optional): _description_. Defaults to None.

        Raises:
            ValueError: If sdlabs_wrapper is not initialized

        Returns:
            List[Dict[str,any]]: List of proposed parameters associated to campaign. Example: [{"iteration":2,"batch":0,"electrolyte_a_cc":10,"electrolyte_b_cc":16},{"iteration":2,"batch":1,"electrolyte_a_cc":10,"electrolyte_b_cc":16}]
        """
        if not self._campaign_id:
            raise ValueError(
                "Ensure to initialize with `initialize_optimization` before"
            )
        sdlabs_recommendations = self._get_parameters(
            max_retries=max_retries, sleep_time_s=sleep_time_s
        )
        if not sdlabs_recommendations:
            raise ValueError(
                "No recommendations could be fetched. Please check the campaign status in SDLabs"
            )
        return sdlabs_recommendations

    def _get_parameters(
        self, max_retries=10, sleep_time_s=10
    ) -> List[models.Recommendation]:
        latest_parameters = []
        for retry in range(max_retries):
            LOGGER.info(
                f"Attempt {retry+1}/{max_retries}: getting latest parameters associated to running campaign"
            )
            latest_parameters = self.wst_api.latest_parameters(
                campaign_ids=[self._campaign_id],
            ).objects
            if latest_parameters:
                LOGGER.debug(f"Latest parameters found: {latest_parameters}")
                break
            LOGGER.debug(f"No parameters found. Retrying in {sleep_time_s} seconds")
            time.sleep(sleep_time_s)
        parameter_map = {
            prm.name: models.Parameter(**prm.to_dict())
            for prm in self.config.parameters
        }
        recommendations = [
            models.Recommendation(_obs_obj=prm_obj, _parameter_map=parameter_map)
            for prm_obj in latest_parameters
        ]

        return recommendations

    def send_measurements(
        self, completed_recommendations: List[models.Recommendation]
    ) -> List[models.Recommendation]:
        """Send measurements.

        Returns:
            List[Dict[str,any]]: List of proposed parameters associated to campaign. Example: [{"iteration":2,"batch":0,"electrolyte_a_cc":10,"electrolyte_b_cc":16},{"iteration":2,"batch":1,"electrolyte_a_cc":10,"electrolyte_b_cc":16}]
        """
        result = self.wst_api.latest_measurements(
            [rec.latest_observation_obj for rec in completed_recommendations]
        )
        for resp_res in result.objects:
            if not resp_res.status == sct.ObservationStatus.OK:
                LOGGER.debug(
                    f"Request {resp_res.id} {resp_res.status} with {resp_res.event.type}: {resp_res.event.message} "
                )
            else:
                # Please note that `successfully submitted` does not necessarily mean `successfully processed`
                #   e.g. your observation may land out of the parameter space if you changed the parameter value,
                #   and you will only be able to see it while fetching for the Campaign's status and events.
                LOGGER.debug(f"Request {resp_res.id} successfully submitted.")

        return result


def initialize_optimization(
    api_key=None,
    spec_file_path: str = None,
    spec_file_content: Dict[str, any] = None,
    inherit_data=None,
    always_restart=None,
) -> SDLabsWrapper:
    cfg: models.OptimizationConfig = config.init(
        config_path=spec_file_path,
        api_key=api_key,
        config_dict=spec_file_content,
    )
    cfg.inherit_data = inherit_data or cfg.inherit_data
    cfg.always_restart = always_restart or cfg.always_restart
    sdlabs_wrapper = SDLabsWrapper(config=cfg)
    sdlabs_wrapper._start_optimization()

    return sdlabs_wrapper


if __name__ == "__main__":
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
