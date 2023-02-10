import logging
import pathlib
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, List

import nexus_sdk as nxs
import scientia_sdk as sct
import yaml

import sdlabs_wrapper.config as config
import sdlabs_wrapper.models as models

LOGGER = logging.getLogger(__name__)
SDLABS_ENDPOINT_URL = "https://enterprise.atinary.com/sdlabs/api/latest"
NEXUS_ENDPOINT_URL = "https://scientia.atinary.com/nexus/api/latest/"


@dataclass
class SDLabsWrapper:
    config: models.OptimizationConfig = None
    _campaign_id: str = None  # campaign id
    _sdlabs_api_client: sct.ApiClient = None  # api client of sdlabs sdk
    _nexus_api_client: nxs.ApiClient = None  # api client of Nexus sdk
    _workstation: sct.WorkstationObj = None  # workstation
    _current_parameter_file_ids: List[str] = None

    def __post_init__(self):
        self.config = self.config or config.init()
        configuration = sct.Configuration(
            host=SDLABS_ENDPOINT_URL,
            api_key={
                "X-API-KEY": self.config.api_key,
            },
        )
        self.sdlabs_api_client = sct.ApiClient(configuration)
        nxs_configuration = nxs.Configuration(
            host=NEXUS_ENDPOINT_URL,
            api_key={
                "X-API-KEY": self.config.api_key,
            },
        )
        self.nexus_api_client = nxs.ApiClient(nxs_configuration)
        self.projects_api = nxs.ProjectsApi(self.nexus_api_client)
        self.files_api = nxs.FilesApi(self.nexus_api_client)

    def _start_optimization(self):
        wst_api = sct.WorkstationApi(self.sdlabs_api_client)
        prm_api = sct.ParameterApi(self.sdlabs_api_client)
        tpl_api = sct.TemplateApi(self.sdlabs_api_client)
        cpg_api = sct.CampaignApi(self.sdlabs_api_client)
        # List workstations
        wsts = wst_api.workstations_list(
            is_public=False, group_id=self.config.sdlabs_group_id
        ).objects
        workstation = next(
            (wst for wst in wsts if wst.name == self.config.optimization_name), None
        )
        if workstation:
            self.workstation = wst_api.workstation_get(workstation.id).object
        else:
            LOGGER.info(
                f"Creating new workstation associated to parameters {self.config.parameters} and measurements {[obj.name for obj in self.config.objectives]}"
            )
            # Create some metadata
            wst_name = self.config.optimization_name
            wst_description = self.config.description
            wst_bandwidth = 99  # How many recommendations can be handled in parallel?
            # specify Nexus connection
            wst_connection = sct.ConnectConfigObj(
                format="yml",
                type=sct.ConnectionType.NEXUS,
                nexus_project_name=self.config.optimization_name,
                # this points to a workstation-specific repository where files will be handled
                user_api_key=self.config.api_key,
            )
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
                prm_api.parameter_create(parameter_obj=prm).object for prm in params
            ]
            # create measurements
            wst_measurements = [obj.name for obj in self.config.objectives]
            # create workstation
            self.workstation = wst_api.workstation_create(
                workstation_obj=sct.WorkstationObj(
                    name=wst_name,
                    description=wst_description,
                    connection=wst_connection,
                    bandwidth=wst_bandwidth,
                    measurements=wst_measurements,
                    parameters=[p.id for p in wst_params],
                )
            ).object
        # check if template exists
        LOGGER.info(f"Linked to workstation '{self.workstation.id}'")
        tpls = tpl_api.templates_list(group_id=self.config.sdlabs_group_id).objects
        template_id = next(
            (
                tpl.id
                for tpl in tpls
                if tpl.name == f"{self.workstation.name} Optimization Template"
            ),
            None,
        )
        if template_id:
            self.template = tpl_api.template_get(template_id=template_id).object
        else:
            # Create template
            opt_api = sct.OptimizerApi(self.sdlabs_api_client)
            obj_api = sct.ObjectiveApi(self.sdlabs_api_client)
            mof_api = sct.MultiObjectiveFunctionApi(self.sdlabs_api_client)
            # create parameters
            tpl_params = [
                prm_api.parameter_copy(
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
            opt_id = opt_api.optimizer_create(
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
                        goal=obj.goal,  # can be 'minimize', 'maximize' or 'target'
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
                                obj._id, **obj.multi_objective_configuration.to_dict()
                            )
                            for obj in self.config.objectives
                        ],
                    )
                ).object.id
            self.template = tpl_api.template_create(
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
                )
            ).object
        # Check if any campaigns are running. If so, take one of them
        states = cpg_api.campaigns_state(
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
                        cpg_api.campaign_operation(
                            campaign_id=running_cpg.id,
                            campaign_operation=sct.CampaignOperation(operation="stop"),
                        )
                    time.sleep(2)

        if not self._campaign_id:

            self._campaign_id = tpl_api.template_run(
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
        self, max_retries=2, sleep_time_s=15
    ) -> List[models.Recommendation]:
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
        prm_nexus_files = self._get_parameter_files(
            max_retries=max_retries, sleep_time_s=sleep_time_s
        )
        recommendations = []
        for sdlabs_recommendation in prm_nexus_files:
            recommendation = models.Recommendation(
                _param_file_id=sdlabs_recommendation["file_id"],
                iteration=sdlabs_recommendation["iteration"],
                batch=sdlabs_recommendation["batch"],
                param_values=sdlabs_recommendation["processes"],
            )
            # rescale values (divide by 10^exponent)
            for prm in self.config.parameters:
                recommendation.param_values[prm.name] = prm.rescale_units_to_user(
                    recommendation.param_values[prm.name]
                )

            recommendations.append(recommendation)
        return recommendations

    def _get_parameter_files(
        self, file_ids: List[str] = None, max_retries=2, sleep_time_s=10
    ) -> List[Dict[str, any]]:
        project_name = self.workstation.connection.nexus_project_name
        project_id = next(
            proj.id
            for proj in self.projects_api.list_projects(
                group_id=self.config.sdlabs_group_id
            ).objects
            if proj.name == project_name
        )
        params = []
        for retry in range(max_retries):
            if params:
                LOGGER.info(f"Successfully fetched '{len(params)}' files ")
                break
            if retry > 0:
                LOGGER.info(f"Retry {retry}. Waiting {sleep_time_s} s for new files...")
                time.sleep(sleep_time_s)
            LOGGER.info(f"Retry {retry}. Fetching parameter files...")
            for param_file in self.files_api.list_files(
                group_type="parameters", project_id=project_id
            ).objects:
                if file_ids and param_file.id not in file_ids:
                    continue
                file_path = pathlib.Path(
                    self.files_api.download_file(file_id=param_file.id)
                )
                # reading the file
                with open(file_path, "r") as content:
                    # content is a yaml
                    param_values = yaml.safe_load(content)
                if param_values["campaign_id"] == self._campaign_id:
                    params.append(
                        {
                            "file_id": param_file.id,
                            "file_name": param_file.name,
                            **param_values,
                        }
                    )

        return params

    def send_measurements(
        self, completed_recommendations: List[models.Recommendation]
    ) -> List[models.Recommendation]:
        """Send measurements.

        Args:
            sdlabs_wrapper (SDLabsWrapper, optional): _description_.

        Raises:
            ValueError: If sdlabs_wrapper is not initialized

        Returns:
            List[Dict[str,any]]: List of proposed parameters associated to campaign. Example: [{"iteration":2,"batch":0,"electrolyte_a_cc":10,"electrolyte_b_cc":16},{"iteration":2,"batch":1,"electrolyte_a_cc":10,"electrolyte_b_cc":16}]
        """
        # reformat to SDLabs (rescale small values)
        for rec in completed_recommendations:
            for prm in self.config.parameters:
                rec.param_values[prm.name] = prm.rescale_units_to_sdlabs(
                    rec.param_values[prm.name]
                )
        if not self._campaign_id:
            raise ValueError(
                "Ensure to initialize with `initialize_optimization` before"
            )
        file_ids = [rec._param_file_id for rec in completed_recommendations]
        param_files = self._get_parameter_files(file_ids=file_ids)
        # Creation of response file
        for measurements in completed_recommendations:
            param_file = next(
                fle
                for fle in param_files
                if fle["file_id"] == measurements._param_file_id
            )
            self._upload_measurements_file(measurements, param_file)

    def _upload_measurements_file(self, measurements, param_file):
        data = {
            **param_file,
            "processes": measurements.param_values,
            "properties": measurements.measurements,
        }
        response_file = pathlib.Path(
            f'{tempfile.gettempdir()}/{param_file["file_name"]}'
        )
        with open(response_file, "w") as content:
            yaml.dump(data, content)
            # Uploading the file
        project_name = self.workstation.connection.nexus_project_name
        project_id = next(
            proj.id
            for proj in self.projects_api.list_projects(
                group_id=self.config.sdlabs_group_id
            ).objects
            if proj.name == project_name
        )
        self.files_api.upload_file(
            project_id,
            "properties",
            upload_file_req=nxs.UploadFileReq(file=str(response_file.resolve())),
        )
        LOGGER.info(
            f'Properties uploaded {param_file["file_id"]}:{param_file["file_name"]}'
        )

        # Remove the temporary file and the parameter file processed in Nexus.
        response_file.unlink()
        self.files_api.delete_file(param_file["file_id"])
        LOGGER.info(
            f'Parameter file deleted {param_file["file_id"]}:{param_file["file_name"]}'
        )


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
        inherit_data=True,
        always_restart=True,
        # spec_file_path=file_path,
        spec_file_content=config_dict,
    )
    for iteration in range(wrapper.config.budget):
        suggestions = wrapper.get_new_suggestions(max_retries=4, sleep_time_s=15)
        LOGGER.info(f"Iteration {iteration+1} New Suggestions: {suggestions}")
        for suggestion in suggestions:
            suggestion.measurements = {}
            for obj in wrapper.config.objectives:
                suggestion.measurements[obj.name] = random.random()
        if suggestions:
            wrapper.send_measurements(suggestions)
