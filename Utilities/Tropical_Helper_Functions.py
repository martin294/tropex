import nvidia_smi  # can be installed via 'pip install nvidia-ml-py3'
import numpy as np
import os
from tensorflow.keras import backend as K
from tensorflow.keras.models import Model
from Utilities.Data_Loader import load_data
from Utilities.Saver import create_directory, get_saving_directory

# To raise an exception on runtime warnings, used for debugging.
np.seterr(all='raise')


def compute_maximal_difference(array1, array2):
    return np.max(np.abs(array1 - array2))


def evaluate_batch_of_tropical_function(data, pos_terms, neg_terms=None):
    pos_result = np.max(np.dot(pos_terms, data), axis=0)
    if neg_terms is not None:
        neg_result = np.max(np.dot(neg_terms, data), axis=0)
        return pos_result, neg_result
    return pos_result


def evaluate_tropical_function(data, pos_terms, neg_terms=None):
    # Input:
    # (data_size, no_data_points)-sized array data
    # (no_labels)-sized lists pos_terms, neg_terms
    # Output:
    # (no_labels, no_data_points)-sized array
    no_labels = len(pos_terms)
    pos_result = [None] * no_labels
    for i in range(no_labels):
        pos_result[i] = np.max(np.dot(pos_terms[i], data), axis=0)
    if neg_terms is not None:
        neg_result = [None] * no_labels
        for i in range(no_labels):
            neg_result[i] = np.max(np.dot(neg_terms[i], data), axis=0)
        return np.vstack(pos_result), np.vstack(neg_result)
    return np.vstack(pos_result)


def evaluate_network_on_subgrouped_data(network, grouped_data):
    output_predictor = K.function([network.layers[0].input],
                                  [network.layers[-2].output])
    x_train_predicted = []
    for data_group in grouped_data:
        for subgroup in data_group:
            x_train_predicted.append(np.max(output_predictor([subgroup])[0], axis=1))
    x_train_predicted = np.hstack(x_train_predicted)
    return x_train_predicted


def get_current_data(network, grouped_data, layer_idx, no_data_groups=None):
    last_layer_index = len(network.layers) - 2
    if layer_idx == last_layer_index:
        current_data = stack_list_with_subgroups(grouped_data)
    else:
        if no_data_groups is None:
            no_data_groups = len(grouped_data)
        current_data = [None] * no_data_groups
        for batch_idx in range(no_data_groups):
            current_data[batch_idx] = predict_data_batchwise(network, grouped_data[batch_idx], 'no_split',
                                                             layer_idx + 3)
        current_data = np.vstack(current_data)
    return prepare_data_for_tropical_function(current_data)


def get_max_data_group_size(arg):
    nvidia_smi.nvmlInit()
    handle = nvidia_smi.nvmlDeviceGetHandleByIndex(0)
    info = nvidia_smi.nvmlDeviceGetMemoryInfo(handle)
    total_memory = info.total
    if total_memory >= 12 * (10 ** 9):
        if arg.pos_or_neg == 'pos_and_neg':
            return 2 ** 12
        else:
            return 2 ** 13
    elif total_memory >= 6 * (10 ** 9):
        if arg.pos_or_neg == 'pos_and_neg':
            return 2 ** 11
        else:
            return 2 ** 12
    else:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        if arg.pos_or_neg == 'pos_and_neg':
            return 2 ** 12
        else:
            return 2 ** 13


def get_last_layer_index(network):
    return len(network.layers) - 2


def get_no_batches(arg, network):
    no_labels = get_no_labels(network)
    if arg.extract_all_dimensions:
        no_batches = no_labels ** 2
    else:
        no_batches = no_labels
    return no_batches


def get_no_labels(network):
    return network.layers[-1].output_shape[1]


def partition_according_to_correct_indices(x_test, indices):
    no_groups = len(x_test)
    x_test_right = [None] * no_groups
    x_test_wrong = [None] * no_groups
    for i in range(no_groups):
        no_subgroups = len(x_test[i])
        x_test_right[i] = [None] * no_subgroups
        x_test_wrong[i] = [None] * no_subgroups
        for j in range(no_subgroups):
            x_test_right[i][j] = x_test[i][j][indices[i][j]]
            x_test_wrong[i][j] = x_test[i][j][np.logical_not(indices[i][j])]
    return x_test_right, x_test_wrong


def reorder_terms(terms, old_labels, new_labels, no_labels, max_data_group_size):
    old_labels = np.squeeze(np.vstack(old_labels))
    new_labels = np.squeeze(np.vstack(new_labels))
    terms = np.vstack(terms)
    new_labels = group_points(new_labels, old_labels, no_labels, max_data_group_size)
    new_labels = stack_list_with_subgroups(new_labels)
    terms = group_points(terms, new_labels, no_labels, max_data_group_size)
    return terms


def group_points(points, labels, no_labels, max_data_group_size=None):
    def ceiling_division(a, b):
        return -(-a // b)

    grouped_points = [None] * no_labels
    for i in range(no_labels):
        grouped_points[i] = points[labels == i]
        if max_data_group_size is not None:
            group_size = grouped_points[i].shape[0]
            no_subgroups = max(ceiling_division(group_size, max_data_group_size), 1)
            grouped_points[i] = np.array_split(grouped_points[i], no_subgroups)
    return grouped_points


def get_grouped_data(arg, network, subgroups=True, data_type=None, get_labels=False, get_data=True):
    if data_type is None:
        data_type = arg.data_type
    data, true_labels = load_data(arg, data_type)
    if arg.data_type == 'training' and arg.data_set == 'CIFAR10':
        data = data[arg.data_points_lower:arg.data_points_upper]
        true_labels = true_labels[arg.data_points_lower:arg.data_points_upper]

    no_labels = np.max(true_labels) + 1
    network_labels = np.argmax(network.predict(data), axis=1)
    if subgroups:
        max_data_group_size = get_max_data_group_size(arg)
    if get_data:
        grouped_data = group_points(data, true_labels, no_labels, max_data_group_size)
        if not get_labels:
            return grouped_data
    if get_labels:
        network_labels = np.concatenate(group_points(true_labels, network_labels, no_labels))
        true_labels = np.concatenate(group_points(true_labels, network_labels, no_labels))
        if not get_data:
            return true_labels, network_labels
    return grouped_data, true_labels, network_labels


def stack_list_with_subgroups(terms):
    stacked_list = []
    for term in terms:
        for subterm in term:
            stacked_list.append(subterm)
    return np.concatenate(stacked_list)


def get_tropical_test_labels(arg, network, grouped_test_data, layer_idx, epoch_number):
    func_dir = get_tropical_function_directory(arg, network, layer_idx, 'test', epoch_number)
    if os.path.isdir(func_dir):
        current_data = get_current_data(network, grouped_test_data, layer_idx)
        pos_terms = load_tropical_function(arg, network, 'training', epoch_number)
        pos_result = evaluate_tropical_function(current_data, pos_terms)
        tropical_test_labels = np.argmax(pos_result, axis=0)
        return tropical_test_labels
    else:
        return None


def load_tropical_function_batch(save_dir, batch_idx, load_negative=False):
    pos_terms = np.load(os.path.join(save_dir, 'pos_label_' + str(batch_idx) + '.npy'))
    if load_negative:
        neg_terms = np.load(os.path.join(save_dir, 'neg_label_' + str(batch_idx) + '.npy'))
        return pos_terms, neg_terms
    return pos_terms


def get_tropical_filename_ending(batch_idx, subgroup_number):
    return '_label_' + str(batch_idx) + '_split_' + str(subgroup_number) + '.npy'


def get_no_subgroups(list_of_file_names, data_group_number):
    return len(list(filter(lambda file_name: 'pos_label_' + str(data_group_number) in file_name, list_of_file_names)))


def load_tropical_function(arg, network, layer_idx, no_labels, data_type, epoch_number, load_negative=False, stacked=False):
    save_dir = get_tropical_function_directory(arg, network, layer_idx, data_type, epoch_number)
    list_of_file_names = os.listdir(save_dir)
    pos_terms = [None] * no_labels
    for data_group_number in range(no_labels):
        no_subgroups = get_no_subgroups(list_of_file_names, data_group_number)
        file_name_ending = get_tropical_filename_ending(data_group_number, 0)
        path = os.path.join(save_dir, 'pos' + file_name_ending)
        if not os.path.isfile(path):
            continue
        else:
            pos_terms[data_group_number] = np.load(path)
            for subgroup_number in range(1, no_subgroups):
                file_name_ending = get_tropical_filename_ending(data_group_number, subgroup_number)
                new_terms = np.load(os.path.join(save_dir, 'pos' + file_name_ending))
                pos_terms[data_group_number] = np.vstack([pos_terms[data_group_number], new_terms])
    pos_terms = list(filter(None.__ne__, pos_terms))
    if load_negative:
        neg_terms = [None] * no_labels
        for data_group_number in range(no_labels):
            no_subgroups = get_no_subgroups(list_of_file_names, data_group_number)
            file_name_ending = get_tropical_filename_ending(data_group_number, 0)
            path = os.path.join(save_dir, 'neg' + file_name_ending)
            if not os.path.isfile(path):
                continue
            else:
                neg_terms[data_group_number] = np.load(path)
                for subgroup_number in range(1, no_subgroups):
                    file_name_ending = get_tropical_filename_ending(data_group_number, subgroup_number)
                    new_terms = np.load(os.path.join(save_dir, 'neg' + file_name_ending))
                    neg_terms[data_group_number] = np.vstack([neg_terms[data_group_number], new_terms])
        neg_terms = list(filter(None.__ne__, neg_terms))
        if stacked:
            return np.vstack(pos_terms), np.vstack(neg_terms)
        else:
            return pos_terms, neg_terms
    if stacked:
        return np.vstack(pos_terms)
    else:
        return pos_terms
    

def predict_data(network, data_batch, flag, layer_idx):
    # predictor = K.function([network.layers[0].input],
    #                        [network.layers[-index].output])
    # predictor([data])[0]
    predictor = Model(inputs=network.layers[0].input, outputs=network.layers[-layer_idx].output)
    if flag == 'no_split':
        return predictor.predict(data_batch)
    elif flag == 0:
        return predictor.predict(data_batch[0])
    else:
        return predictor.predict(data_batch[1])


def predict_data_batchwise(network, data_batch, flag, layer_idcs):
    predictor = K.function([network.input], [network.layers[-layer_idcs].output])
    if flag == 'no_split':
        return predictor([data_batch])[0]
    elif flag == 0:
        return predictor([data_batch[0]])[0]
    else:
        return predictor([data_batch[1]])[0]


def predict_activation_patterns_batchwise(network, data_batch, flag, layer_idcs):
    network_output = predict_data_batchwise(network, data_batch, flag, layer_idcs)
    activation_patterns = list(map(lambda x: x > 0, network_output))
    return activation_patterns


def get_layer_type(layer):
    return layer.name.split('_')[0]


def get_activation_patterns(arg, network, data_group=None):
    # activation_patterns = no_data_groups x no_data_subgroups x no_relevant_layers
    def turn_data_into_activation_patterns(data):
        current_activation_patterns = []
        for layer in reversed(layers_without_softmax):
            layer_type = get_layer_type(layer)
            if layer_type in ['leaky', 're', 'activation']:
                data_after_layer = data.pop()
                current_activation_patterns.append(data_after_layer <= 0)
            elif layer_type == 'max':
                data_after_layer = data.pop()
                data_before_layer = data.pop()
                repeated_data = np.repeat(np.repeat(data_after_layer, repeats=2, axis=1), repeats=2, axis=2)

                def pad_data_before_layer(data_before_layer, repeated_data, axis):
                    shape = data_before_layer.shape
                    if repeated_data.shape[axis] == shape[axis] + 1:
                        new_array = np.full(shape[0:axis] + (1,) + shape[axis+1:], np.NINF)
                        data_before_layer = np.concatenate([data_before_layer, new_array], axis=axis)
                        padding = True
                    else:
                        padding = False
                    return data_before_layer, padding

                data_before_layer, vertical_padding = pad_data_before_layer(data_before_layer, repeated_data, 1)
                data_before_layer, horizontal_padding = pad_data_before_layer(data_before_layer, repeated_data, 2)

                aps = (repeated_data != data_before_layer)
                # The following guarantees that in each maxpooling square, at least 3 out of 4 elements of B are set to zero.
                if vertical_padding:
                    aps[:, -1, :, :] = True
                if horizontal_padding:
                    aps[:, :, -1, :] = True
                aps[:, ::2, 1::2, :] = np.logical_or(aps[:, ::2, 1::2, :], np.logical_not(aps[:, ::2, ::2]))
                aps[:, 1::2, ::2, :] = np.logical_or(aps[:, 1::2, ::2, :], np.logical_not(aps[:, ::2, ::2]))
                aps[:, 1::2, ::2, :] = np.logical_or(aps[:, 1::2, ::2, :], np.logical_not(aps[:, ::2, 1::2]))
                aps[:, 1::2, 1::2, :] = np.logical_or(aps[:, 1::2, 1::2, :], np.logical_not(aps[:, ::2, ::2]))
                aps[:, 1::2, 1::2, :] = np.logical_or(aps[:, 1::2, 1::2, :], np.logical_not(aps[:, ::2, 1::2]))
                aps[:, 1::2, 1::2, :] = np.logical_or(aps[:, 1::2, 1::2, :], np.logical_not(aps[:, 1::2, ::2]))

                if vertical_padding:
                    aps = aps[:, 0:-1, :, :]
                if horizontal_padding:
                    aps = aps[:, :, 0:-1, :]

                current_activation_patterns.append(aps)
        return current_activation_patterns

    input = network.input
    outputs = []
    layers_without_softmax = network.layers[0:-1]
    for layer_idx in range(len(layers_without_softmax)):
        layer = layers_without_softmax[layer_idx]
        layer_type = get_layer_type(layer)
        if layer_type in ['leaky', 're', 'activation']:
            outputs.append(layer.output)
        elif layer_type == 'max':
            outputs.append(layers_without_softmax[layer_idx - 1].output)
            outputs.append(layer.output)
    os.environ["CUDA_VISIBLE_DEVICES"] = arg.gpu
    predictor = K.function([input], outputs)
    predicted_data = predictor([data_group])
    activation_patterns = turn_data_into_activation_patterns(predicted_data)
    K.clear_session()
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    return activation_patterns


def get_epoch_numbers(arg):
    if arg.epochs == 'all':
        return ['00', '01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12', '13', '14', '15', '16',
                '17', '18', '19', '20', '22', '24', '26', '28', '30', '32', '34', '36', '38', '40', '42', '44', '46',
                '48', '50', '55', '60', '65', '70', '75', '80', '85', '90', '95', '100', '105', '110', '115', '120',
                '125', '130', '135', '140', '145', '150', '155', '160', '165', '170', '175', '180', '185', '190', '195',
                '200']
    elif arg.epochs == 'special':
        return ['160', '165', '170', '175', '180', '185', '190', '195',
                '200']
    else:
        return [None]


def get_associated_training_points(arg, network, x_test, x_train):
    pos_terms = np.vstack(load_tropical_function(arg, network, 'training', None))
    associated_training_points = []
    for subgroup in x_test:
        subgroup = prepare_data_for_tropical_function(subgroup)
        indices = np.argmax(np.dot(pos_terms, subgroup), axis=0)
        associated_training_points.append(x_train[indices])
    return associated_training_points


def get_no_data_points_per_label(grouped_data):
    return list(map(lambda y: sum(map(lambda x: x.shape[0], y)), grouped_data))


def get_no_data_subgroups_per_data_group(activation_patterns):
    return list(map(lambda x: len(x), activation_patterns))


def get_tropical_function_directory(arg, folder_name, data_type, epoch_number):
    save_dir = get_saving_directory(arg)
    func_dir = create_directory(save_dir, data_type.capitalize())
    if arg.epochs == 'all' or arg.epochs == 'special':
        func_dir = create_directory(func_dir, 'all_epochs', 'epoch_' + str(epoch_number))
    if arg.extract_all_dimensions:
        all_dimensions_string = '_all_dimensions'
    else:
        all_dimensions_string = ''
    func_dir = create_directory(func_dir, folder_name + all_dimensions_string)
    return func_dir


def flatten_and_stack(variable_part, bias_part):
    variable_part = np.reshape(variable_part, newshape=[variable_part.shape[0], -1])
    result = np.hstack([bias_part, variable_part])
    return result


def prepare_data_for_tropical_function(data):
    return flatten_and_stack(data, np.ones_like(data[:, 0:1, 0, 0])).transpose()


def normalize(matrix):
    return matrix / np.linalg.norm(matrix, axis=1)[:, np.newaxis]


def compute_1_1_euclidean_distances(terms_0, terms_1):
    return np.linalg.norm(terms_0 - terms_1, axis=1)


def compute_1_1_angles(terms_0, terms_1):
    terms_0 = normalize(terms_0)
    terms_1 = normalize(terms_1)
    return np.arccos(np.clip(np.einsum('ij,ij->i', terms_0, terms_1), -1, 1))


def shift_array(array, v_shift, h_shift, filling='zeros'):
    shifted_array = np.zeros_like(array)
    if v_shift > 0:  # down shift
        shifted_array[:, v_shift:, :, :] = array[:, 0:(-v_shift), :, :]
        if filling == 'nearest':
            shifted_array[:, 0:v_shift, :, :] = array[:, 0:1, :, :]
        else:
            shifted_array[:, 0:v_shift, :, :] = 0
    elif v_shift < 0:  # up shift
        shifted_array[:, 0:v_shift, :, :] = array[:, (-v_shift):, :, :]
        if filling == 'nearest':
            shifted_array[:, v_shift:, :, :] = array[:, -1:, :, :]
        else:
            shifted_array[:, v_shift:, :, :] = 0
    else:
        shifted_array[:, :, :, :] = array[:, :, :, :]
    if h_shift > 0:  # right shift
        shifted_array[:, :, h_shift:, :] = shifted_array[:, :, 0:(-h_shift), :]
        if filling == 'nearest':
            shifted_array[:, :, 0:h_shift, :] = shifted_array[:, :, 0:1, :]
        else:
            shifted_array[:, :, 0:h_shift, :] = 0
    elif h_shift < 0:  # left shift
        shifted_array[:, :, 0:h_shift, :] = shifted_array[:, :, (-h_shift):, :]
        if filling == 'nearest':
            shifted_array[:, :, h_shift:, :] = shifted_array[:, :, -1:, :]
        else:
            shifted_array[:, :, h_shift:, :] = 0
    else:
        shifted_array[:, :, :, :] = shifted_array[:, :, :, :]
    return shifted_array


def shift_tropical_function(terms, v_shift, h_shift, filling='zeros'):
    no_labels = len(terms)
    for i in range(no_labels):
        bias = terms[i][:, 0:1]
        main = terms[i][:, 1:]
    logger.info("Shifts in [-6, -3, 0, 3, 6]^2")
    function_path = get_function_path(arg, last_layer_index, transformation_path)
    out = [None] * no_labels
    for k in range(no_labels):
        pos_terms_k = np.load(function_path + 'pos_label_' + str(k) + '.npy')
        k_bias = pos_terms_k[:, 0:1]
        k_main = pos_terms_k[:, 1:]
        k_main = k_main.reshape([-1, 32, 32, 3])
        shifts = [-6, -3, 0, 3, 6]
        for v_shift in shifts:
            for h_shift in shifts:
                if v_shift != 0 or h_shift != 0:
                    shifted_terms = shift_array(k_main, v_shift, h_shift, shift_type)
                    shifted_terms = shifted_terms.reshape([-1, 3072])
                    shifted_terms = np.hstack([k_bias, shifted_terms])
                    pos_terms_k = np.vstack([pos_terms_k, shifted_terms])
        out[k] = np.max(np.dot(pos_terms_k, current_data), axis=0)
    tropical_augmented_test_labels = np.argmax(out, axis=0)
    network_agreement_augmented = sum(tropical_augmented_test_labels == network_labels) / len(network_labels)
