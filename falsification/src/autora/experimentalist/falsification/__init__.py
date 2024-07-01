import numpy as np
import pandas as pd
import torch
from torch.autograd import Variable
from typing import Optional, Iterable, Union

from autora.variable import ValueType, VariableCollection
from autora.experimentalist.falsification.utils import class_to_onehot, get_iv_limits, align_dataframe_to_ivs
from autora.experimentalist.falsification.popper_net import PopperNet, train_popper_net_with_model, train_popper_net
from autora.utils.deprecation import deprecated_alias
from sklearn.preprocessing import StandardScaler


def pool(
    model,
    reference_conditions: Union[pd.DataFrame, np.ndarray],
    reference_observations: Union[pd.DataFrame, np.ndarray],
    metadata: VariableCollection,
    num_samples: int = 100,
    training_epochs: int = 1000,
    optimization_epochs: int = 1000,
    training_lr: float = 1e-3,
    optimization_lr: float = 1e-3,
    limit_offset: float = 0,  # 10**-10,
    limit_repulsion: float = 0,
    plot: bool = False,
):
    """
    A pooler that generates samples for independent variables with the objective of maximizing the
    (approximated) loss of the model. The samples are generated by first training a neural network
    to approximate the loss of a model for all patterns in the training data.
    Once trained, the network is then inverted to generate samples that maximize the approximated
    loss of the model.

    Note: If the pooler returns samples that are close to the boundaries of the variable space,
    then it is advisable to increase the limit_repulsion parameter (e.g., to 0.000001).

    Args:
        model: Scikit-learn model, could be either a classification or regression model
        reference_conditions: data that the model was trained on
        reference_observations: labels that the model was trained on
        metadata: Meta-data about the dependent and independent variables
        num_samples: number of samples to return
        training_epochs: number of epochs to train the popper network for approximating the
        error fo the model
        optimization_epochs: number of epochs to optimize the samples based on the trained
        popper network
        training_lr: learning rate for training the popper network
        optimization_lr: learning rate for optimizing the samples
        limit_offset: a limited offset to prevent the samples from being too close to the value
        boundaries
        limit_repulsion: a limited repulsion to prevent the samples from being too close to the
        allowed value boundaries
        plot: print out the prediction of the popper network as well as its training loss

    Returns: Sampled pool

    """

    # format input

    if isinstance(reference_conditions, pd.DataFrame):
        reference_conditions = align_dataframe_to_ivs(reference_conditions, metadata.independent_variables)

    reference_conditions_np = np.array(reference_conditions)
    if len(reference_conditions_np.shape) == 1:
        reference_conditions_np = reference_conditions_np.reshape(-1, 1)

    x = np.empty([num_samples, reference_conditions_np.shape[1]])

    reference_observations = np.array(reference_observations)
    if len(reference_observations.shape) == 1:
        reference_observations = reference_observations.reshape(-1, 1)

    if metadata.dependent_variables[0].type == ValueType.CLASS:
        # find all unique values in reference_observations
        num_classes = len(np.unique(reference_observations))
        reference_observations = class_to_onehot(reference_observations, n_classes=num_classes)

    reference_conditions_tensor = torch.from_numpy(reference_conditions_np).float()

    iv_limit_list = get_iv_limits(reference_conditions_np, metadata)

    popper_net, model_loss = train_popper_net_with_model(model,
                                              reference_conditions_np,
                                              reference_observations,
                                              metadata,
                                              iv_limit_list,
                                              training_epochs,
                                              training_lr,
                                              plot)

    # now that the popper network is trained we can sample new data points
    # to sample data points we need to provide the popper network with an initial
    # condition we will sample those initial conditions proportional to the loss of the current
    # model

    # feed average model losses through softmax
    # model_loss_avg= torch.from_numpy(np.mean(model_loss.detach().numpy(), axis=1)).float()
    softmax_func = torch.nn.Softmax(dim=0)
    probabilities = softmax_func(model_loss)
    # sample data point in proportion to model loss
    transform_category = torch.distributions.categorical.Categorical(probabilities)

    popper_net.freeze_weights()

    for condition in range(num_samples):

        index = transform_category.sample()
        input_sample = torch.flatten(reference_conditions_tensor[index, :])
        popper_input = Variable(input_sample, requires_grad=True)

        # invert the popper network to determine optimal experiment conditions
        for optimization_epoch in range(optimization_epochs):
            # feedforward pass on popper network
            popper_prediction = popper_net(popper_input)
            # compute gradient that maximizes output of popper network
            # (i.e. predicted loss of original model)
            popper_loss_optim = -popper_prediction
            popper_loss_optim.backward()

            with torch.no_grad():

                # first add repulsion from variable limits
                for idx in range(len(input_sample)):
                    iv_value = popper_input[idx]
                    iv_limits = iv_limit_list[idx]
                    dist_to_min = np.abs(iv_value - np.min(iv_limits))
                    dist_to_max = np.abs(iv_value - np.max(iv_limits))
                    # deal with boundary case where distance is 0 or very small
                    dist_to_min = np.max([dist_to_min, 0.00000001])
                    dist_to_max = np.max([dist_to_max, 0.00000001])
                    repulsion_from_min = limit_repulsion / (dist_to_min**2)
                    repulsion_from_max = limit_repulsion / (dist_to_max**2)
                    iv_value_repulsed = (
                        iv_value + repulsion_from_min - repulsion_from_max
                    )
                    popper_input[idx] = iv_value_repulsed

                # now add gradient for theory loss maximization
                delta = -optimization_lr * popper_input.grad
                popper_input += delta

                # finally, clip input variable from its limits
                for idx in range(len(input_sample)):
                    iv_raw_value = input_sample[idx]
                    iv_limits = iv_limit_list[idx]
                    iv_clipped_value = np.min(
                        [iv_raw_value, np.max(iv_limits) - limit_offset]
                    )
                    iv_clipped_value = np.max(
                        [
                            iv_clipped_value,
                            np.min(iv_limits) + limit_offset,
                        ]
                    )
                    popper_input[idx] = iv_clipped_value
                popper_input.grad.zero_()

        # add condition to new experiment sequence
        for idx in range(len(input_sample)):
            iv_limits = iv_limit_list[idx]

            # first clip value
            iv_clipped_value = np.min([iv_raw_value, np.max(iv_limits) - limit_offset])
            iv_clipped_value = np.max(
                [iv_clipped_value, np.min(iv_limits) + limit_offset]
            )
            # make sure to convert variable to original scale
            iv_clipped_scaled_value = iv_clipped_value

            x[condition, idx] = iv_clipped_scaled_value

    return iter(x)

def sample(
    conditions: Union[pd.DataFrame, np.ndarray],
    model,
    reference_conditions: Union[pd.DataFrame, np.ndarray],
    reference_observations: Union[pd.DataFrame, np.ndarray],
    metadata: VariableCollection,
    num_samples: Optional[int] = None,
    training_epochs: int = 1000,
    training_lr: float = 1e-3,
    plot: bool = False,
):
    """
    A Sampler that generates samples of experimental conditions with the objective of maximizing the
    (approximated) loss of a model relating experimental conditions to observations. The samples are generated by first
    training a neural network to approximate the loss of a model for all patterns in the training data.
    Once trained, the network is then provided with the candidate samples of experimental conditions and the selects
    those with the highest loss.

    Args:
        conditions: The candidate samples of experimental conditions to be evaluated.
        model: Scikit-learn model, could be either a classification or regression model
        reference_conditions: Experimental conditions that the model was trained on
        reference_observations: Observations that the model was trained to predict
        metadata: Meta-data about the dependent and independent variables specifying the experimental conditions
        num_samples: Number of samples to return
        training_epochs: Number of epochs to train the popper network for approximating the
        error of the model
        training_lr: Learning rate for training the popper network
        plot: Print out the prediction of the popper network as well as its training loss

    Returns: Samples with the highest loss

    """

    # format input

    if isinstance(conditions, Iterable) and not isinstance(conditions, pd.DataFrame):
        conditions = np.array(list(conditions))

    condition_pool_copy = conditions.copy()
    conditions = np.array(conditions)
    reference_observations = np.array(reference_observations)
    reference_conditions = np.array(reference_conditions)
    if len(reference_conditions.shape) == 1:
        reference_conditions = reference_conditions.reshape(-1, 1)

    # get target pattern for popper net
    model_predict = getattr(model, "predict_proba", None)
    if callable(model_predict) is False:
        model_predict = getattr(model, "predict", None)

    if callable(model_predict) is False or model_predict is None:
        raise Exception("Model must have `predict` or `predict_proba` method.")

    predicted_observations = model_predict(reference_conditions)
    if isinstance(predicted_observations, np.ndarray) is False:
        try:
            predicted_observations = np.array(predicted_observations)
        except Exception:
            raise Exception("Model prediction must be convertable to numpy array.")
    if predicted_observations.ndim == 1:
        predicted_observations = predicted_observations.reshape(-1, 1)

    new_conditions, scores = falsification_score_sample_from_predictions(
        conditions,
        predicted_observations,
        reference_conditions,
        reference_observations,
        metadata,
        num_samples,
        training_epochs,
        training_lr,
        plot,
    )

    if isinstance(condition_pool_copy, pd.DataFrame):
        new_conditions = pd.DataFrame(new_conditions, columns=condition_pool_copy.columns)

    return new_conditions


def falsification_score_sample(
    conditions: Union[pd.DataFrame, np.ndarray],
    model,
    reference_conditions: Union[pd.DataFrame, np.ndarray],
    reference_observations: Union[pd.DataFrame, np.ndarray],
    metadata: Optional[VariableCollection] = None,
    num_samples: Optional[int] = None,
    training_epochs: int = 1000,
    training_lr: float = 1e-3,
    plot: bool = False,
):
    """
    A Sampler that generates samples of experimental conditions with the objective of maximizing the
    (approximated) loss of a model relating experimental conditions to observations. The samples are generated by first
    training a neural network to approximate the loss of a model for all patterns in the training data.
    Once trained, the network is then provided with the candidate samples of experimental conditions and the selects
    those with the highest loss.

    Args:
        conditions: The candidate samples of experimental conditions to be evaluated.
        model: Scikit-learn model, could be either a classification or regression model
        reference_conditions: Experimental conditions that the model was trained on
        reference_observations: Observations that the model was trained to predict
        metadata: Meta-data about the dependent and independent variables specifying the experimental conditions
        num_samples: Number of samples to return
        training_epochs: Number of epochs to train the popper network for approximating the
        error of the model
        training_lr: Learning rate for training the popper network
        plot: Print out the prediction of the popper network as well as its training loss

    Returns:
        new_conditions: Samples of experimental conditions with the highest loss
        scores: Normalized falsification scores for the samples

    """

    if isinstance(conditions, Iterable) and not isinstance(conditions, pd.DataFrame):
        conditions = np.array(list(conditions))

    condition_pool_copy = conditions.copy()
    conditions = np.array(conditions)
    reference_conditions = np.array(reference_conditions)
    reference_observations = np.array(reference_observations)

    if len(reference_conditions.shape) == 1:
        reference_conditions = reference_conditions.reshape(-1, 1)

    predicted_observations = model.predict(reference_conditions)

    new_conditions, new_scores =  falsification_score_sample_from_predictions(conditions,
                                                        predicted_observations,
                                                        reference_conditions,
                                                        reference_observations,
                                                        metadata,
                                                        num_samples,
                                                        training_epochs,
                                                        training_lr,
                                                        plot)

    if isinstance(condition_pool_copy, pd.DataFrame):
        sorted_conditions = pd.DataFrame(new_conditions, columns=condition_pool_copy.columns)
    else:
        sorted_conditions = pd.DataFrame(new_conditions)

    sorted_conditions["score"] = new_scores

    return sorted_conditions


def falsification_score_sample_from_predictions(
    conditions: Union[pd.DataFrame, np.ndarray],
    predicted_observations: Union[pd.DataFrame, np.ndarray],
    reference_conditions: Union[pd.DataFrame, np.ndarray],
    reference_observations: np.ndarray,
    metadata: Optional[VariableCollection] = None,
    num_samples: Optional[int] = None,
    training_epochs: int = 1000,
    training_lr: float = 1e-3,
    plot: bool = False,
):
    """
    A Sampler that generates samples of experimental conditions with the objective of maximizing the
    (approximated) loss of a model relating experimental conditions to observations. The samples are generated by first
    training a neural network to approximate the loss of a model for all patterns in the training data.
    Once trained, the network is then provided with the candidate samples of experimental conditions and the selects
    those with the highest loss.

    Args:
        conditions: The candidate samples of experimental conditions to be evaluated.
        predicted_observations: Prediction obtained from the model for the set of reference experimental conditions
        reference_conditions: Experimental conditions that the model was trained on
        reference_observations: Observations that the model was trained to predict
        metadata: Meta-data about the dependent and independent variables specifying the experimental conditions
        num_samples: Number of samples to return
        training_epochs: Number of epochs to train the popper network for approximating the
        error of the model
        training_lr: Learning rate for training the popper network
        plot: Print out the prediction of the popper network as well as its training loss

    Returns:
        new_conditions: Samples of experimental conditions with the highest loss
        scores: Normalized falsification scores for the samples

    """

    conditions = np.array(conditions)
    reference_conditions = np.array(reference_conditions)
    reference_observations = np.array(reference_observations)

    if len(conditions.shape) == 1:
        conditions = conditions.reshape(-1, 1)

    reference_conditions = np.array(reference_conditions)
    if len(reference_conditions.shape) == 1:
        reference_conditions = reference_conditions.reshape(-1, 1)

    reference_observations = np.array(reference_observations)
    if len(reference_observations.shape) == 1:
        reference_observations = reference_observations.reshape(-1, 1)

    if num_samples is None:
        num_samples = conditions.shape[0]

    if metadata is not None:
        if metadata.dependent_variables[0].type == ValueType.CLASS:
            # find all unique values in reference_observations
            num_classes = len(np.unique(reference_observations))
            reference_observations = class_to_onehot(reference_observations, n_classes=num_classes)

    # create list of IV limits
    iv_limit_list = get_iv_limits(reference_conditions, metadata)

    popper_net, model_loss = train_popper_net(predicted_observations,
                                              reference_conditions,
                                              reference_observations,
                                              metadata,
                                              iv_limit_list,
                                              training_epochs,
                                              training_lr,
                                              plot)

    # now that the popper network is trained we can assign losses to all data points to be evaluated
    popper_input = Variable(torch.from_numpy(conditions)).float()
    Y = popper_net(popper_input).detach().numpy().flatten()
    scaler = StandardScaler()
    score = scaler.fit_transform(Y.reshape(-1, 1)).flatten()

    # order rows in Y from highest to lowest
    sorted_conditions = conditions[np.argsort(score)[::-1]]
    sorted_score = score[np.argsort(score)[::-1]]

    return sorted_conditions[0:num_samples], sorted_score[0:num_samples]

falsification_pool = pool
falsification_pool.__doc__ = """Alias for pool"""
falsification_pooler = deprecated_alias(falsification_pool, "falsification_pooler")

falsification_sample = sample
falsification_pool.__doc__ = """Alias for sample"""
falsification_sampler = deprecated_alias(falsification_sample, "falsification_sampler")
falsification_score_sampler = deprecated_alias(falsification_score_sample, "falsification_score_sampler")
falsification_score_sampler_from_predictions = deprecated_alias(falsification_score_sample_from_predictions, "falsification_score_sampler_from_predictions")