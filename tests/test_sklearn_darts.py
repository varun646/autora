from copy import deepcopy

import numpy as np
import pytest
import torch
from skl.darts_execution_monitor import BasicExecutionMonitor
from sklearn.model_selection import GridSearchCV, train_test_split

from autora.skl.darts import PRIMITIVES, DARTSRegressor, DARTSType, ValueType


def generate_noisy_constant_data(
    const: float = 0.5, epsilon: float = 0.01, num: int = 1000, seed: int = 42
):
    X = np.expand_dims(np.linspace(start=0, stop=1, num=num), 1)
    y = np.random.default_rng(seed).normal(loc=const, scale=epsilon, size=num)
    return X, y, const, epsilon


def generate_constant_data(const: float = 0.5, num: int = 1000):
    X = np.expand_dims(np.linspace(start=0, stop=1, num=num), 1)
    y = const * np.ones(num)
    return X, y, const


def generate_noisy_linear_data(
    const: float = 0.5,
    gradient=0.25,
    epsilon: float = 0.01,
    num: int = 1000,
    seed: int = 42,
    start: float = 0,
    stop: float = 1,
):
    X = np.expand_dims(np.linspace(start=start, stop=stop, num=num), 1)
    y = (
        (gradient * X.ravel())
        + const
        + np.random.default_rng(seed).normal(loc=0, scale=epsilon, size=num)
    )
    return (
        X,
        y,
        const,
        gradient,
        epsilon,
    )


def test_constant_model():

    X, y, const, epsilon = generate_noisy_constant_data()

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    estimator = DARTSRegressor(num_graph_nodes=1)

    estimator.fit(X_train, y_train)

    assert estimator is not None

    for y_pred_i in np.nditer(estimator.predict(X_test)):
        (const - (5.0 * epsilon)) <= y_pred_i <= (const + (5.0 * epsilon))

    print(estimator.network_)


def test_enum_string_inputs():

    X, y, const, epsilon = generate_noisy_constant_data()

    kwargs = dict(
        num_graph_nodes=1,
        max_epochs=1,
        arch_updates_per_epoch=1,
        param_updates_per_epoch=1,
    )

    DARTSRegressor(darts_type="fair", **kwargs).fit(X, y)
    DARTSRegressor(darts_type=DARTSType.FAIR, **kwargs).fit(X, y)
    DARTSRegressor(darts_type="original", **kwargs).fit(X, y)
    DARTSRegressor(darts_type=DARTSType.ORIGINAL, **kwargs).fit(X, y)

    DARTSRegressor(output_type="probability", **kwargs).fit(X, y)
    DARTSRegressor(output_type=ValueType.PROBABILITY, **kwargs).fit(X, y)
    DARTSRegressor(output_type=ValueType.PROBABILITY_SAMPLE, **kwargs).fit(X, y)
    DARTSRegressor(output_type="probability_distribution", **kwargs).fit(X, y)
    DARTSRegressor(output_type=ValueType.PROBABILITY_DISTRIBUTION, **kwargs).fit(X, y)
    with pytest.raises(NotImplementedError):
        DARTSRegressor(output_type="class", **kwargs).fit(X, y)
    with pytest.raises(NotImplementedError):
        DARTSRegressor(output_type=ValueType.CLASS, **kwargs).fit(X, y)


def test_model_repr():

    X, y, const, epsilon = generate_noisy_constant_data()

    kwargs = dict(
        max_epochs=1,
        arch_updates_per_epoch=1,
        param_updates_per_epoch=1,
    )

    print(DARTSRegressor(num_graph_nodes=1, **kwargs).fit(X, y).model_repr())
    print(DARTSRegressor(num_graph_nodes=2, **kwargs).fit(X, y).model_repr())
    print(DARTSRegressor(num_graph_nodes=4, **kwargs).fit(X, y).model_repr())
    print(DARTSRegressor(num_graph_nodes=8, **kwargs).fit(X, y).model_repr())
    print(DARTSRegressor(num_graph_nodes=16, **kwargs).fit(X, y).model_repr())


def test_primitive_selection():
    X, y, const, epsilon = generate_noisy_constant_data()

    kwargs = dict(
        num_graph_nodes=1,
        max_epochs=1,
        arch_updates_per_epoch=1,
        param_updates_per_epoch=1,
    )

    DARTSRegressor(primitives=["add", "subtract", "none"], **kwargs).fit(X, y)
    DARTSRegressor(primitives=PRIMITIVES, **kwargs).fit(X, y)
    with pytest.raises(KeyError):
        KeyError, DARTSRegressor(primitives=["doesnt_exist"], **kwargs).fit(X, y)


def test_fit_with_fixed_architecture():
    X, y, _, _, _ = generate_noisy_linear_data(
        start=-5, stop=+5, const=10.0, gradient=20.0
    )
    X1, y1, _, _, _ = generate_noisy_linear_data(
        start=-5, stop=+5, const=10.0, gradient=5.0
    )

    # Initialize the fitter
    regressor = DARTSRegressor(
        primitives=["linear", "mult"],
        num_graph_nodes=1,
    )

    # First fit: normal fitting
    regressor.set_params(
        max_epochs=500,
        arch_updates_per_epoch=1,
        param_updates_per_epoch=50,
        param_updates_for_sampled_model=1000,
    )
    regressor.fit(X, y)
    network_weights_initial = deepcopy(regressor.network_.alphas_normal)
    equation_initial = regressor.model_repr()
    print(equation_initial)

    # Refit by setting epochs to one and arch updates to zero, and fit some different data
    regressor.set_params(
        max_epochs=0,
        param_updates_for_sampled_model=1000,
    )
    regressor.fit(X1, y1)
    network_weights_refitted = deepcopy(regressor.network_.alphas_normal)
    equation_refitted = regressor.model_repr()
    print(equation_refitted)

    # Architecture weights should be the same
    assert torch.all(network_weights_initial.eq(network_weights_refitted))

    # ... but equations should be different
    assert equation_initial != equation_refitted

    # Now refit using the "sampler".
    regressor.set_params(
        max_epochs=0,
        param_updates_for_sampled_model=1000,
        sampling_strategy="sample",
    )
    regressor.fit(X1, y1)
    equation_resampled = regressor.model_repr()
    print(equation_resampled)

    # Now return to the original settings and recover the original results.
    regressor.set_params(
        max_epochs=0,
        param_updates_for_sampled_model=1000,
        sampling_strategy="max",
    )
    regressor.fit(X, y)
    network_weights_max_recovered = deepcopy(regressor.network_.alphas_normal)
    equation_max_recovered = regressor.model_repr()
    print(equation_max_recovered)
    assert equation_initial == equation_max_recovered
    assert torch.all(network_weights_initial.eq(network_weights_max_recovered))


def test_metaparam_optimization():

    X, y, const = generate_constant_data()

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    estimator = GridSearchCV(
        estimator=DARTSRegressor(),
        cv=2,
        param_grid=[
            {
                "max_epochs": [10, 50],
                "arch_updates_per_epoch": [5, 10, 15],
                "param_updates_per_epoch": [5, 10, 15],
                "num_graph_nodes": [1, 2, 3],
            }
        ],
    )

    estimator.fit(X_train, y_train)

    print(estimator.best_params_)
    print(X_test)
    print(estimator.predict(X_test))

    for y_pred_i in np.nditer(estimator.predict(X_test)):
        assert (const - 0.01) < y_pred_i < (const + 0.01)

    print(estimator.predict(X_test))


def test_execution_monitor():
    import matplotlib.pyplot as plt

    X, y, const, epsilon = generate_noisy_constant_data()

    kwargs = dict()

    execution_monitor_0 = BasicExecutionMonitor()

    DARTSRegressor(
        primitives=["add", "subtract", "none", "mult", "logistic"],
        execution_monitor=execution_monitor_0.execution_monitor,
        num_graph_nodes=3,
        max_epochs=100,
        param_updates_per_epoch=100,
        **kwargs
    ).fit(X, y)
    execution_monitor_0.display()

    execution_monitor_1 = BasicExecutionMonitor()
    DARTSRegressor(
        primitives=["add", "ln"],
        num_graph_nodes=5,
        max_epochs=100,
        param_updates_per_epoch=100,
        execution_monitor=execution_monitor_1.execution_monitor,
        **kwargs
    ).fit(X, y)
    execution_monitor_1.display()

    plt.show()
