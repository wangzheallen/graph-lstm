import lstm
import numpy as np
import random

class MLSL:
    def __init__(self,max_depth, hidden_layer_sizes, input_sizes):
        self.lstm_stack = [lstm.LSTM() for l in range(max_depth)]
        for l in range(max_depth):
            self.lstm_stack[l].initialize(input_sizes[l] + (0 if l== max_depth -1 else hidden_layer_sizes[l + 1]), hidden_layer_sizes[l])
        self.hidden_layer_sizes = hidden_layer_sizes
        self.input_sizes = input_sizes
        # we need the following structures, when training with momentum and/or adadelta to keep track of the sum of dW at each level
        # in order to update the momentum_dW or the adadelta parameters of the respective LSTM modules
        self.number_of_nodes_per_level = [0 for l in range(max_depth)]
        self.sum_of_dWs = [0.0 for l in range(max_depth)]
        self.sum_tot_sq_gradient =  [0.0 for l in range(max_depth)]
        self.sum_tot_gradient_weight = [0.0 for l in range(max_depth)]
        self.sum_tot_sq_delta = [0.0 for l in range(max_depth)]
        self.sum_tot_delta_weight = [0.0 for l in range(max_depth)]

    """ Forward instance function through the multi layer LSTM architecture"""
    def forward_instance(self, instance_node, current_depth, max_depth, sequence_function = ["none","none","none"]):
        if instance_node.get_number_of_children() == 0:
            return -100 * np.ones(self.hidden_layer_sizes[current_depth]) # no children signifier vector
        input_sequence = np.array([])
        children_sequence = get_sequence(instance_node.get_children(), sequence_function[current_depth])
        for item in children_sequence:
            feature_vector = item.get_feature_vector()
            """ If we are not at the very bottom we need to get input from LSTM at the next level"""
            LSTM_output_from_below = np.array([])
            if current_depth < max_depth:
                 LSTM_output_from_below = self.forward_instance(item, current_depth + 1, max_depth).reshape(self.hidden_layer_sizes[current_depth +1]) # recursive call
            full_feature_vector = np.concatenate((LSTM_output_from_below, feature_vector)) # concatenate feature vector and input from LSTM output below
            # concatenate current feature vector to input sequence for the LSTM
            input_sequence = np.concatenate((input_sequence,full_feature_vector))
        # forward the input sequence to this depth's LSTM
        input_sequence = input_sequence.reshape(instance_node.get_number_of_children(), 1, len(full_feature_vector))
        _, _, Y, cache = self.lstm_stack[current_depth]._forward(input_sequence)
        instance_node.cache = cache
        # we also need to save the sequence
        instance_node.children_sequence = children_sequence
        return softmax(Y)

    def calculate_backward_gradients(self,instance_node, derivative, current_depth, max_depth, learning_method_vector):
        dX, g, _, _ = self.lstm_stack[current_depth].backward_return_vector_no_update(d = derivative, cache = instance_node.cache)
        instance_node.gradient = g
        if current_depth == max_depth:
            return
        counter = 0
        for item in instance_node.children_sequence:
            if item.cache is None:
                continue
            self.calculate_backward_gradients(item, dX[counter,:,0:self.hidden_layer_sizes[current_depth + 1]], current_depth + 1, max_depth = max_depth, learning_method_vector = learning_method_vector)
            counter += 1

    def update_LSTM_weights_steady_rate(self,instance_node, current_depth, max_depth, learning_rate_vector, learning_method_vector, momentum_vector, adadelta_parameters):
        if not instance_node.gradient is None:
            dW = - learning_rate_vector[current_depth] * instance_node.gradient
            self.sum_of_dWs[current_depth] += dW
            self.number_of_nodes_per_level[current_depth] += 1
        if current_depth == max_depth:
            return
        for item in instance_node.children_sequence:
            self.update_LSTM_weights(item, current_depth + 1, max_depth, learning_rate_vector, learning_method_vector, momentum_vector, adadelta_parameters)

    def update_LSTM_weights_with_momentum(self,instance_node, current_depth, max_depth, learning_rate_vector, learning_method_vector, momentum_vector, adadelta_parameters):
        if not instance_node.gradient is None:
            if self.lstm_stack[current_depth].momentum_dW is None: # initialize momentum of LSTM to zero
                self.lstm_stack[current_depth].momentum_dW = np.zeros(self.lstm_stack[current_depth].WLSTM.shape)
            dW = - learning_rate_vector[current_depth] * instance_node.gradient + momentum_vector[current_depth] * self.lstm_stack[current_depth].momentum_dW
            self.lstm_stack[current_depth].WLSTM += dW
            self.sum_of_dWs[current_depth] += dW
            self.number_of_nodes_per_level[current_depth] += 1
        if current_depth == max_depth:
            return
        for item in instance_node.children_sequence:
            self.update_LSTM_weights(item, current_depth + 1, max_depth, learning_rate_vector, learning_method_vector, momentum_vector, adadelta_parameters)

    def update_LSTM_weights_adadelta(self,instance_node, current_depth, max_depth, learning_rate_vector, learning_method_vector, momentum_vector, adadelta_parameters):
        # obtain adadelta parameters
        decay = adadelta_parameters[current_depth]["decay"]
        epsilon = adadelta_parameters[current_depth]["epsilon"]
        learning_factor = adadelta_parameters[current_depth]["learning_factor"]
        # do the adadelta updates
        if not instance_node.gradient is None:
            instance_node.tot_sq_gradient = self.lstm_stack[current_depth].tot_sq_gradient * decay + np.sum(np.square(instance_node.gradient))
            instance_node.tot_gradient_weight = self.lstm_stack[current_depth].tot_gradient_weight * decay + 1.0
            # Computes the speed.
            rms_delta = np.sqrt((self.lstm_stack[current_depth].tot_sq_delta + epsilon) / (self.lstm_stack[current_depth].tot_delta_weight + epsilon))
            rms_gradient = np.sqrt((instance_node.tot_sq_gradient + epsilon) / (instance_node.tot_gradient_weight + epsilon))
            s = rms_delta / rms_gradient
            # Computes the delta.
            delta = s * instance_node.gradient
            instance_node.tot_sq_delta = self.lstm_stack[current_depth].tot_sq_delta * decay + np.sum(np.square(delta))
            instance_node.tot_delta_weight = self.lstm_stack[current_depth].tot_delta_weight * decay + 1.0
            # Finally, updates the weights.
            dW = - delta * learning_factor
            self.sum_of_dWs[current_depth] += dW
            self.number_of_nodes_per_level[current_depth] += 1
            self.sum_tot_sq_gradient[current_depth] += instance_node.tot_sq_gradient
            self.sum_tot_gradient_weight[current_depth] += instance_node.tot_gradient_weight
            self.sum_tot_sq_delta[current_depth] += instance_node.tot_sq_delta
            self.sum_tot_delta_weight[current_depth] += instance_node.tot_delta_weight
        if current_depth == max_depth:
            return
        for item in instance_node.children_sequence:
            self.update_LSTM_weights(item, current_depth + 1, max_depth, learning_rate_vector, learning_method_vector, momentum_vector, adadelta_parameters)


    def update_LSTM_weights(self,instance_node, current_depth, max_depth, learning_rate_vector, learning_method_vector, momentum_vector, adadelta_parameters):
        training_methods = ["steady_rate", "momentum", "adadelta"]
        if learning_method_vector[current_depth] not in training_methods:
            print "FATAL: unknown training method"
            exit()
        if learning_method_vector[current_depth] == "steady_rate":
            self.update_LSTM_weights_steady_rate(instance_node, current_depth, max_depth, learning_rate_vector, learning_method_vector, momentum_vector, adadelta_parameters)
        if learning_method_vector[current_depth] == "momentum":
            self.update_LSTM_weights_with_momentum(instance_node, current_depth, max_depth, learning_rate_vector,learning_method_vector, momentum_vector, adadelta_parameters)
        if learning_method_vector[current_depth] == "adadelta":
            self.update_LSTM_weights_adadelta(instance_node, current_depth, max_depth, learning_rate_vector,learning_method_vector, momentum_vector, adadelta_parameters)

    """ Stochastic gradient descent with
        a tree unfolding as training instance
    """
    def sgd_train_mlsl(self, root, target, max_depth, objective_function, learning_rate_vector, learning_method_vector, momentum_vector, adadelta_parameters):
        # first pass the instance root one forward so that all internal LSTM states
        # get calculated and stored in "cache" field
        self.sum_of_dWs = [0.0 for l in range(max_depth + 1)] # initializing total dW for each training instance
        self.number_of_nodes_per_level = [0.0 for l in range(max_depth+ 1)]
        self.sum_tot_sq_gradient =  [0.0 for l in range(max_depth + 1)]
        self.sum_tot_gradient_weight = [0.0 for l in range(max_depth + 1)]
        self.sum_tot_sq_delta = [0.0 for l in range(max_depth + 1)]
        self.sum_tot_delta_weight = [0.0 for l in range(max_depth + 1)]
        Y = self.forward_instance(root, current_depth = 0, max_depth= max_depth)
        deriv = get_objective_derivative(output = Y, target = target, objective = objective_function)
        self.calculate_backward_gradients(root, deriv, 0, max_depth, learning_method_vector)
        self.update_LSTM_weights(root, 0, max_depth, learning_rate_vector,learning_method_vector, momentum_vector, adadelta_parameters)
        # updating the weights of the LSTM modules and
        # updating momentum_dW of LSTM modules with sums of dWs
        # and the other variables for adadelta
        # these momentum/adadelta specific updates happen regardless of whether we use steady rate, momentum, or adadelta
        # if we use steady rate those variables play no role in the computation of dW
        for d in range(max_depth + 1):
            self.lstm_stack[d].WLSTM += self.sum_of_dWs[d] / self.number_of_nodes_per_level[d]
            self.lstm_stack[d].momentum_dW = self.sum_of_dWs[d] / self.number_of_nodes_per_level[d]
            self.lstm_stack[d].tot_gradient_weight = self.sum_tot_delta_weight[d] / self.number_of_nodes_per_level[d]
            self.lstm_stack[d].tot_sq_gradient = self.sum_tot_sq_gradient[d] / self.number_of_nodes_per_level[d]
            self.lstm_stack[d].tot_delta_weight = self.sum_tot_delta_weight[d] / self.number_of_nodes_per_level[d]
            self.lstm_stack[d].tot_sq_delta = self.sum_tot_sq_delta[d] / self.number_of_nodes_per_level[d]

    """
    trains MLSL with stochastic gradient descent
    by imposing class balance, i.e. shows equal number of examples of all classes to the network during training
    """
    def train_model_force_balance(self, training_set, no_of_instances, max_depth, objective_function, learning_rate_vector, learning_method_vector, momentum_vector = None, adadelta_parameters = None):
        counter = 0
        if no_of_instances == 0:
            return
        for item in get_balanced_training_set(training_set, self.hidden_layer_sizes[0]):
            if item.get_number_of_children() == 0:
                continue
            target = np.zeros((1,self.hidden_layer_sizes[0]))
            target[0,item.get_label()] = 1.0
            self.sgd_train_mlsl(item, target, max_depth, objective_function, learning_rate_vector, learning_method_vector, momentum_vector, adadelta_parameters)
            counter += 1
            if counter % 1000 == 0:
                print "Training has gone over", counter, " instances.."
            if counter == no_of_instances:
                break

    def test_model(self, test_set, max_depth):
        guesses = 0
        hits = 0
        found = {}
        missed = {}
        misclassified = {}
        for item in test_set:
            Y = self.forward_instance(item, 0 , max_depth)
            if Y is None:
                continue
            print Y
            predicted_label = Y.argmax()
            real_label = item.get_label()
            print "Predicted label ", predicted_label , " real label", real_label
            guesses += 1
            hits += 1 if predicted_label == real_label else 0
            if predicted_label == real_label:
                if real_label not in found:
                    found[real_label] = 1
                else:
                    found[real_label] += 1
            if predicted_label != real_label:
                if real_label not in missed:
                    missed[real_label] = 1
                else:
                    missed[real_label] += 1
                if predicted_label not in misclassified:
                    misclassified[predicted_label] = 1
                else:
                    misclassified[predicted_label] += 1
        print "LSTM results"
        print "============================================================="
        print "Predicted correctly ", hits , "over ", guesses, " instances."
        recall_list = []
        recall_dict = {}
        precision_dict = {}
        found_labels = set(found.keys())
        missed_labels = set(missed.keys())
        all_labels = found_labels.union(missed_labels)
        for label in all_labels:
            no_of_finds = float((0 if label not in found else found[label]))
            no_of_missed = float((0 if label not in missed else missed[label]))
            no_of_misclassified = float((0 if label not in misclassified else misclassified[label]))
            recall =  no_of_finds / (no_of_finds + no_of_missed)
            precision = no_of_finds / (no_of_finds + no_of_misclassified)
            recall_dict[label] = recall
            precision_dict[label] = precision
            recall_list.append(recall)
        avg_recall = np.mean(recall_list)
        print "Average recall ", np.mean(recall_list)
        if len(all_labels) == 2: # compute F-1 score for binary classification
            for label in all_labels:
                print "F-1 score for label ", label, " is : ", 2 * (precision_dict[label] * recall_dict[label]) / (precision_dict[label] + recall_dict[label])
        return avg_recall



# the following class represents nodes of the unfoldings
# the MLSL module understands and can train and test on tree instances that are encoded as objects of this class

class Instance_node:
    def __init__(self, feature_vector = None, label = None, id = None):
        self.id = id
        self.feature_vector = feature_vector
        self.label = label
        self.cache = None
        self.children = []
        self.children_sequence = [] # Stores the specific order by which the items were fed into the LSTM to update weights correctly
        self.gradient = None
        # momentum variable for the momentum method
        self.momentum = None
        # variables for the adadelta method
        self.tot_gradient_weight, self.tot_delta_weight = 0, 0
        self.tot_sq_gradient, self.tot_sq_delta = 0, 0

    def set_label(self, label):
        self.label = label

    def get_number_of_children(self):
        return len(self.children)

    def get_label(self):
        return self.label

    def get_children(self):
        return self.children

    def get_feature_vector(self):
        return self.feature_vector


""" Generator that returns items from training set
    equally balanced among classes"""
def get_balanced_training_set(training_set, no_of_classes):
    # make bucket of classes to sample from
    buckets = {}
    buckets_current_indexes ={}
    for i in range(0, no_of_classes):
        buckets[i] = []
        buckets_current_indexes[i] = 0
    for item in training_set:
        category = item.get_label()
        buckets[category].append(item)
    while True:
        for i in range(0,no_of_classes):
            if len(buckets[i]) == 0: # if a class has no representatives, continue
                continue
            if buckets_current_indexes[i] == len(buckets[i]):
                buckets_current_indexes[i] = 0
            yield buckets[i][buckets_current_indexes[i]]
            buckets_current_indexes[i] += 1

def get_sequence(children_list, sequence_function):
    if sequence_function == "shuffle":
        res = list(children_list)
        random.shuffle(res)
    if sequence_function == "none":
        res = list(children_list)
    return res


def softmax(w, t = 1.0):
    e = np.exp(np.array(w) / t)
    dist = e / np.sum(e)
    return dist

def get_objective_derivative(output, target, objective):
    if objective == "softmax_classification":
        return output - target