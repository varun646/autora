#!/usr/bin/env python
import warnings
from typing import Callable, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pytest  # noqa: 401

from aer_bms.skl.bms import BMSRegressor

warnings.filterwarnings("ignore")

non_interchangeable_primitives = [
    "none",
    "add",
    "subtract",
    "mult",
    "div",
    "pow",
    "logistic",
    "exp",
    "relu",
    "cos",
    "cosh",
    "sin",
    "sinh",
    "tan",
    "tanh",
    "pow2",
    "pow3",
    "sqrt",
    "abs",
    "fac",
]


def generate_x(start=-1, stop=1, num=500):
    x = np.expand_dims(np.linspace(start=start, stop=stop, num=num), 1)
    return x


def generate_2d_x(start=-1, stop=1, num=40):
    step = abs(stop - start) / num
    x2 = np.mgrid[start:stop:step, start:stop:step].reshape(2, -1).T
    return x2


def generate_2d_pos_x(start=0.5, stop=1, num=40):
    step = abs(stop - start) / num
    x2 = np.mgrid[start:stop:step, start:stop:step].reshape(2, -1).T
    return x2


def generate_pos_x(start=0.5, stop=1, num=500):
    x = np.expand_dims(np.linspace(start=start, stop=stop, num=num), 1)
    return x


def generate_x_log(start=-1, stop=1, num=500, base=10):
    x = np.expand_dims(np.logspace(start=start, stop=stop, num=num, base=base), 1)
    return x


def transform_through_primitive_add_2d(x: np.ndarray) -> np.ndarray:
    return x[:, 0] + x[:, 1]


def transform_through_primitive_subtract_2d(x: np.ndarray) -> np.ndarray:
    return x[:, 0] - x[:, 1]


def transform_through_primitive_mult_2d(x: np.ndarray) -> np.ndarray:
    return np.multiply(x[:, 0], x[:, 1])


def transform_through_primitive_div_2d(x: np.ndarray) -> np.ndarray:
    return np.multiply(x[:, 0], np.reciprocal(x[:, 1]))


def transform_through_primitive_pow_2d(x: np.ndarray) -> np.ndarray:
    return np.power(x[:, 0], x[:, 1])


def transform_through_primitive_pow2(x: np.ndarray) -> np.ndarray:
    return x**2


def transform_through_primitive_pow3(x: np.ndarray) -> np.ndarray:
    return x**3


def transform_through_primitive_sqrt(x: np.ndarray) -> np.ndarray:
    return np.sqrt(x)


def transform_through_primitive_abs(x: np.ndarray) -> np.ndarray:
    return np.abs(x)


def transform_through_primitive_fac(x: np.ndarray) -> np.ndarray:
    y = []
    for x_i in x:
        y.append(np.math.gamma(x_i[0] + 1.0))
    y_hat = np.array(y)
    return np.expand_dims(y_hat, 1)


def transform_through_primitive_none(x: np.ndarray) -> np.ndarray:
    return x * 0


def transform_through_primitive_add(x: np.ndarray) -> np.ndarray:
    return x


def transform_through_primitive_subtract(x: np.ndarray) -> np.ndarray:
    return -x


def transform_through_primitive_relu(x: np.ndarray):
    y = x.copy()
    y[x < 0.0] = 0.0
    return y


def transform_through_primitive_sigmoid(x: np.ndarray):
    y = 1.0 / (1.0 + np.exp(-x))
    return y


def transform_through_primitive_exp(x: np.ndarray):
    y = np.exp(x)
    return y


def transform_through_primitive_cos(x: np.ndarray):
    y = np.cos(x)
    return y


def transform_through_primitive_cosh(x: np.ndarray):
    y = np.cosh(x)
    return y


def transform_through_primitive_sin(x: np.ndarray):
    y = np.sin(x)
    return y


def transform_through_primitive_sinh(x: np.ndarray):
    y = np.sinh(x)
    return y


def transform_through_primitive_tan(x: np.ndarray):
    y = np.tan(x)
    return y


def transform_through_primitive_tanh(x: np.ndarray):
    y = np.tanh(x)
    return y


def transform_through_primitive_softplus(x: np.ndarray, beta=1.0):
    y = np.log(1 + np.exp(beta * x)) / beta
    return y


def transform_through_primitive_softminus(x: np.ndarray, beta=1.0):
    y = x - np.log(1 + np.exp(beta * x)) / beta
    return y


def transform_through_primitive_inverse(x: np.ndarray):
    y = 1.0 / x
    return y


def transform_through_primitive_ln(x: np.ndarray):
    y = np.log(x)
    return y


def transform_through_primitive_mult(x: np.ndarray, coefficient=5.0):
    y = coefficient * x
    return y


def run_test_primitive_fitting(
    X: np.ndarray,
    transformer: Callable,
    expected_primitive: str,
    primitives: Sequence[str],
    verbose: bool = True,
):
    y = transformer(X)
    regressor = BMSRegressor()
    regressor.fit(X, y.ravel())
    if verbose:
        y_predict = regressor.predict(X)
        for x_i in X.T[
            :,
        ]:
            plot_results(x_i, y, y_predict)
        print(regressor.model_)
        print(regressor.pms.trees)


def plot_results(X, y, y_predict):
    plt.figure()
    plt.plot(X, y, "o")
    plt.plot(X, y_predict, "-")
    plt.show()


def test_primitive_fitting_restricted_none():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_none,
        "none",
        primitives=["none", "add", "subtract"],
    )


def test_primitive_fitting_restricted_add():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_add,
        "add",
        primitives=["none", "add", "subtract"],
    )


def test_primitive_fitting_restricted_subtract():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_subtract,
        "subtract",
        primitives=["none", "add", "subtract"],
    )


def test_primitive_fitting_pow2():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_pow2,
        "pow2",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_pow3():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_pow3,
        "pow3",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_sqrt():
    run_test_primitive_fitting(
        generate_pos_x(),
        transform_through_primitive_sqrt,
        "sqrt",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_abs():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_abs,
        "abs",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_fac():
    run_test_primitive_fitting(
        generate_pos_x(),
        transform_through_primitive_fac,
        "fac",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_none():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_none,
        "none",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_add():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_add,
        "add",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_subtract():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_subtract,
        "subtract",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_relu():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_relu,
        "relu",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_sigmoid():
    run_test_primitive_fitting(
        generate_x(-10, +10),
        transform_through_primitive_sigmoid,
        "logistic",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_exp():
    run_test_primitive_fitting(
        generate_x(),
        transform_through_primitive_exp,
        "exp",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_cos():
    run_test_primitive_fitting(
        generate_x(start=0, stop=2 * np.pi),
        transform_through_primitive_cos,
        "cos",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_cosh():
    run_test_primitive_fitting(
        generate_x(start=0, stop=2 * np.pi),
        transform_through_primitive_cosh,
        "cosh",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_sin():
    run_test_primitive_fitting(
        generate_x(start=0, stop=2 * np.pi),
        transform_through_primitive_sin,
        "sin",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_sinh():
    run_test_primitive_fitting(
        generate_x(start=0, stop=2 * np.pi),
        transform_through_primitive_sinh,
        "sinh",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_tan():
    run_test_primitive_fitting(
        generate_x(start=0, stop=2 * np.pi),
        transform_through_primitive_tan,
        "tan",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_tanh():
    run_test_primitive_fitting(
        generate_x(start=0, stop=2 * np.pi),
        transform_through_primitive_tanh,
        "tanh",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_add_2d():
    run_test_primitive_fitting(
        generate_2d_x(),
        transform_through_primitive_add_2d,
        "add",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_subtract_2d():
    run_test_primitive_fitting(
        generate_2d_x(),
        transform_through_primitive_subtract_2d,
        "subtract",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_mult_2d():
    run_test_primitive_fitting(
        generate_2d_x(),
        transform_through_primitive_mult_2d,
        "mult",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_div_2d():
    run_test_primitive_fitting(
        generate_2d_pos_x(),
        transform_through_primitive_div_2d,
        "div",
        primitives=non_interchangeable_primitives,
    )


def test_primitive_fitting_pow_2d():
    run_test_primitive_fitting(
        generate_2d_pos_x(),
        transform_through_primitive_pow_2d,
        "pow",
        primitives=non_interchangeable_primitives,
    )


if __name__ == "__main__":
    print(generate_2d_x())
    print(transform_through_primitive_add_2d(generate_2d_x()))
    test_primitive_fitting_add_2d()
