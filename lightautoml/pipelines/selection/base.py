from copy import copy, deepcopy
from typing import Optional, List, Sequence, Any, Tuple, Union

from log_calls import record_history
from pandas import Series

from lightautoml.validation.base import TrainValidIterator
from ..features.base import FeaturesPipeline
from ..utils import map_pipeline_names
from ...dataset.base import LAMLDataset
from ...ml_algo.base import MLAlgo
from ...ml_algo.tuning.base import ParamsTuner, DefaultTuner
from ...ml_algo.utils import tune_and_fit_predict


@record_history()
class ImportanceEstimator:
    """
    Abstract class.
    Object that estimates feature importances.
    """

    def __init__(self):
        self.raw_importances = None

    # Change signature here to be compatible with MLAlgo
    def fit(self, *args: Any, **kwargs: Any):
        raise NotImplementedError

    def get_features_score(self) -> Series:
        """
        Get features importances.

        Returns:
            Hash table (dict or pd.Series), keys - str features names values - array of floats.
        """
        return self.raw_importances


@record_history()
class SelectionPipeline:
    """
    Abstract class.
    Instance should accept train/valid datatsets and select features.
    """

    @property
    def is_fitted(self) -> bool:
        """
        Check if is fitted

        Returns:
            `bool`
        """
        return self._selected_features is not None

    @property
    def selected_features(self) -> List[str]:
        """
        Get selected features

        Returns:
            list of feature names.

        """
        assert self._selected_features is not None, 'Should be fitted first'
        return self._selected_features

    @property
    def in_features(self) -> List[str]:
        """
        Features input to the selector.

        Returns:
            list of input features.

        """
        assert self._in_features is not None, 'Should be fitted first'
        return self._in_features

    @property
    def dropped_features(self) -> List[str]:
        """
        Features that was dropped.

        Returns:
            list of dropped features.

        """
        included = set(self._selected_features)
        return [x for x in self._in_features if x not in included]

    def __init__(self,
                 features_pipeline: Optional[FeaturesPipeline] = None,
                 ml_algo: Optional[Union[MLAlgo, Tuple[MLAlgo, ParamsTuner]]] = None,
                 imp_estimator: Optional[ImportanceEstimator] = None,
                 fit_on_holdout: bool = False, **kwargs: Any):
        """
        Create features selection pipeline.

        Args:
            features_pipeline: composition of feature transforms.
            ml_algo: Tuple (MlAlgo, ParamsTuner).
            imp_estiamtor: feature importance estimator.
            fit_on_holdout: if use the holdout iterator.
            **kwargs: ignored.

        """
        self.features_pipeline = features_pipeline
        self._fit_on_holdout = fit_on_holdout

        self.ml_algo = None
        self._empty_algo = None
        if ml_algo is not None:
            try:
                self.ml_algo, self.tuner = ml_algo
            except (TypeError, ValueError):
                self.ml_algo, self.tuner = ml_algo, DefaultTuner()

            if not self.ml_algo.is_fitted:
                self._empty_algo = deepcopy(self.ml_algo)

        self.imp_estimator = imp_estimator
        self._selected_features = None
        self._in_features = None
        self.mapped_importances = None

    def perform_selection(self,
                          train_valid: Optional[TrainValidIterator]):
        """
        Method is used to perform selection based on features pipeline and ml model.
        Should save _selected_features attribute in the end of working.

        Raises:
            NotImplementedError.
        """
        raise NotImplementedError

    def fit(self, train_valid: TrainValidIterator):
        """
        Find features selection for given dataset based on features pipeline and ml model.

        Args:
            train_valid: dataset iterator.

        """
        if not self.is_fitted:

            if self._fit_on_holdout:
                train_valid = train_valid.convert_to_holdout_iterator()

            self._in_features = train_valid.features
            if self.features_pipeline is not None:
                train_valid = train_valid.apply_feature_pipeline(self.features_pipeline)

            preds = None
            if self.ml_algo is not None:
                if self.ml_algo.is_fitted:
                    assert list(self.ml_algo.features) == list(train_valid.features), \
                        'Features in feated MLAlgo should match exactly'
                else:
                    self.ml_algo, preds = tune_and_fit_predict(self.ml_algo, self.tuner, train_valid)

            if self.imp_estimator is not None:
                self.imp_estimator.fit(train_valid, self.ml_algo, preds)

            self.perform_selection(train_valid)

    def select(self, dataset: LAMLDataset) -> LAMLDataset:
        """
        Take selected features from giving dataset and creates new dataset.

        Args:
            dataset: dataset for feature selection.

        Returns:
            new dataset.

        """
        selected_features = copy(self.selected_features)
        # Add features that forces input
        sl_set = set(selected_features)
        roles = dataset.roles
        for col in (x for x in dataset.features if x not in sl_set):
            if roles[col].force_input:
                if col not in sl_set:
                    selected_features.append(col)

        return dataset[:, self.selected_features]

    def map_raw_feature_importances(self, raw_importances: Series):
        """
        Calculate input feature importances. Calculated as sum of importances on different levels of pipeline.

        Args:
            raw_importances: importances of output features.

        """
        mapped = map_pipeline_names(self.in_features, raw_importances.index)
        mapped_importance = Series(raw_importances.values, index=mapped)

        self.mapped_importances = mapped_importance.groupby(level=0).sum().sort_values(ascending=False)

    def get_features_score(self):
        """
        Get input feature importances.

        Returns:
            Series with importances in not ascending order.
        """
        return self.mapped_importances


@record_history()
class EmptySelector(SelectionPipeline):
    """
    Empty selector - perform no selection, just save input features names.
    """

    def __init__(self):
        """
        Empty selector - perform no selection, just save input features names
        """
        super().__init__()

    def perform_selection(self,
                          train_valid: Optional[TrainValidIterator]):
        """
        Just save input features names.

        Args:
            train_valid: used for getting features names.

        """
        self._selected_features = train_valid.features


@record_history()
class PredefinedSelector(SelectionPipeline):
    """
    Empty selector - perform no selection, just save input features names.
    """

    def __init__(self, columns_to_select: Sequence[str]):
        """
        Args:
            columns_to_select: columns will be selected.

        """
        super().__init__()
        self.columns_to_select = set(columns_to_select)

    def perform_selection(self,
                          train_valid: Optional[TrainValidIterator]):
        """
        Select only specified columns.

        Args:
            train_valid: used for validation of features presence.

        """
        assert len(self.columns_to_select) == len(self.columns_to_select.intersection(set(train_valid.features))), \
            'Columns to select not match with dataset features'
        self._selected_features = list(self.columns_to_select)


@record_history()
class ComposedSelector(SelectionPipeline):
    """
    Perform composition of selections.
    """

    def __init__(self, selectors: Sequence[SelectionPipeline]):
        """
        Args:
            selectors: sequence of selectors.

        """
        super().__init__()
        self.selectors = selectors

    def fit(self, train_valid: Optional[TrainValidIterator] = None):
        """
        Select features.

        Args:
            train_valid: dataset iterator.

        """
        for selector in self.selectors:
            train_valid = train_valid.apply_selector(selector)

        self._in_features = self.selectors[0].in_features
        self.perform_selection(train_valid)

    def perform_selection(self, train_valid: Optional[TrainValidIterator]):
        """
        Defines selected features.

        Args:
            train_valid: ignored.

        """
        self._selected_features = self.selectors[-1].selected_features

    def get_features_score(self):
        """
        Get mapped input features importances.

        """
        return self.selectors[-1].mapped_importances