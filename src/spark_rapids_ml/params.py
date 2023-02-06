from typing import Any, Dict, List, TypeVar, Union

from pyspark.ml.param import Param, Params, TypeConverters

from spark_rapids_ml.utils import _get_default_params_from_func

P = TypeVar("P", bound="_CumlParams")


class HasNumWorkers(Params):
    """
    Mixin for param num_workers: number of Spark CuML workers, where each worker corresponds to a
    Spark task.
    """

    num_workers = Param(
        Params._dummy(),  # type: ignore
        "num_workers",
        "(cuML) number of Spark CuML workers, where each CuML worker corresponds to one Spark task.",
        TypeConverters.toInt,
    )

    def __init__(self) -> None:
        super(HasNumWorkers, self).__init__()

    def getNumWorkers(self) -> int:
        """
        Gets the value of num_workers or its default value.
        """
        return self.getOrDefault(self.num_workers)


class _CumlClass(object):
    """
    Base class for all _CumlEstimator and _CumlModel implemenations.

    Defines helper methods for mapping Spark ML Params to CuML class parameters.
    """

    @classmethod
    def _cuml_cls(cls) -> List[type]:
        """
        Return a list of cuML python counterpart class names, which will be used to
        auto-generate Spark params.
        """
        raise NotImplementedError

    @classmethod
    def _param_excludes(cls) -> List[str]:
        """
        Return a list of cuML class parameters which should not be auto-populated into the
        Spark class.

        Example
        -------

        .. code-block::python

            return [
                "handle",
                "output_type",
            ]

        """
        return []

    @classmethod
    def _param_mapping(cls) -> Dict[str, str]:
        """
        Return a mapping of Spark ML Param names to CuML parameter names, which is used maintain
        associations from Spark params to CuML parameters.

        Example
        -------

        .. code-block::python

            # For PCA
            return {
                "k": "n_components"
            }
        """
        return {}

    @classmethod
    def _param_value_mapping(cls) -> Dict[str, Dict[str, Union[str, None]]]:
        """
        Return a dictionary of CuML parameter names and their mapping of Spark ML Param string
        values to CuML string values.

        If the mapped value is None, then the Spark value is unsupported, and an error will be
        raised.

        Example
        -------

        .. code-block:: python

            # For LinearRegression
            return {
                "loss": {
                    "squaredError": "squared_loss",
                    "huber": None,
                },
                "solver": {
                    "auto": "eig",
                    "normal": "eig",
                    "l-bfgs": None,
                }
            }

        """
        return {}

    @classmethod
    def _get_cuml_params_default(cls) -> Dict[str, Any]:
        """
        Inspect the __init__ function of associated _cuml_cls() to return a dictionary of
        parameter names and their default values.
        """
        params = {}
        for cls_type in cls._cuml_cls():
            params.update(
                _get_default_params_from_func(cls_type, cls._param_excludes())
            )
        return params


class _CumlParams(_CumlClass, HasNumWorkers):
    """
    Common parameters for all Spark CUML algorithms, along with utilties for synchronizing
    between Spark ML Params and cuML class parameters.
    """

    cuml_params: Dict[str, Any] = {}

    def setNumWorkers(self: P, value: int) -> P:
        """
        Sets the value of :py:attr:`num_workers`.
        """
        return self._set(num_workers=value)

    def initialize_cuml_params(self) -> None:
        """
        Set the default values of CuML parameters to match their Spark equivalents.
        """
        # initialize cuml_params with defaults from CuML
        self.cuml_params = self._get_cuml_params_default()

        # update default values from Spark ML Param equivalents
        param_map = self._param_mapping()

        for spark_param, cuml_param in param_map.items():
            if self.hasDefault(spark_param):
                value = self.getOrDefault(spark_param)
                self._set_cuml_value(cuml_param, value)

    def set_params(self: P, **kwargs: Any) -> P:
        """
        Set the kwargs as Spark ML Params and/or CuML parameters, while maintaining parameter
        and value mappings defined by the _CumlClass.
        """
        param_map = self._param_mapping()

        # raise error if setting both sides of a param mapping
        for spark_param, cuml_param in param_map.items():
            if (
                spark_param != cuml_param
                and spark_param in kwargs
                and cuml_param in kwargs
            ):
                raise ValueError(
                    f"'{cuml_param}' is an alias of '{spark_param}', set one or the other."
                )

        for k, v in kwargs.items():
            if self.hasParam(k):
                # standard Spark ML Param
                self._set(**{str(k): v})  # type: ignore
                if k in param_map:
                    # also set matching CuML parameter, if exists
                    self._set_cuml_value(param_map[k], v)
            if k in self.cuml_params and not self.hasParam(k):
                # cuml param
                self.cuml_params[k] = v
                for spark_param, cuml_param in param_map.items():
                    if k == cuml_param:
                        # also set matching Spark Param, if exists
                        # TODO: map cuml values back to Spark equivalents?
                        self._set(**{str(spark_param): v})

            if (
                not self.hasParam(k)
                and k not in param_map
                and k not in self.cuml_params
            ):
                raise ValueError(f"Unsupported param '{k}'.")
        return self

    def clear(self, param: Param) -> None:
        """
        Reset a Spark ML Param to its default value, setting matching CuML parameter, if exists.
        """
        super().clear(param)
        param_map = self._param_mapping()
        if param.name in param_map:
            self.cuml_params[param_map[param.name]] = self.getOrDefault(param.name)

    def _copy_cuml_params(self: P, to: P) -> P:
        """
        Copy this instance's cuml_params values into another instance, only setting parameters
        which already exist in the other instance.  This is intended to mirror the behavior of
        :py:func:`Params._copyValues()`.

        Parameters
        ----------
        to : :py:class:`_CumlParams`
            Other instance to copy parameter values into.

        Returns
        -------
        :py:class:`_CumlParams`
            Other instance.
        """
        for k, v in self.cuml_params.items():
            if k in to.cuml_params:
                to.cuml_params[k] = v
        return to

    def _set_cuml_value(self, k: str, v: Any) -> None:
        """
        Set a cuml_params parameter with a value.

        If the value originated from a Spark ML Param, and a value mapping exists, the parameter
        will be set to the mapped value.  Generally, this is only useful for string/enum types.

        Parameters
        ----------
        k : str
            Name of a cuml_param parameter.
        v : Any
            Value to assign to the cuml_param parameter, which may be mapped to another value.

        Raises
        ------
        ValueError
            If a value mapping exists, but the mapped value is None, this means that there is
            no equivalent value for the CuML side, so an exception is raised.
        """
        value_map = self._param_value_mapping()
        if k not in value_map:
            # no value mapping required
            self.cuml_params[k] = v
        else:
            # value map exists
            supported_values = set([x for x in value_map[k].values() if x is not None])
            if v in supported_values:
                # already a valid value
                self.cuml_params[k] = v
            else:
                # try to map to a valid value
                mapped_v = value_map[k].get(v, None)
                if mapped_v:
                    self.cuml_params[k] = mapped_v
                else:
                    raise ValueError(
                        f"Value '{v}' for '{k}' param is unsupported, expected: {supported_values}"
                    )
