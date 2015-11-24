import numpy as np
from enum import Enum

import classifier, inspector

class Struct:
    def __init__(self, **entries): 
        self.__dict__.update(entries)

class ITrainer(object):    
    def train(sample, voting_quality_threshold, comparision_threshold,\
              filtering_type, combining_type, skip_selection,\
              logger = None):
        pass
    
    def forecast(train_sample, test_sample, logger = None):
        pass
    
    def get_gescription(voting_quality_threshold):
        pass
    
class NullLogger(object):
    def __init__(self):
        pass
    def push(self, string):
        pass

    def flush(self):
        pass
    
    
class PrintLogger(object):
    def __init__(self):
        pass
    
    def push(self, string):
        print string
    
    def flush(self):
        pass

        
class FileLogger(object):
    def __init__(self, filename):
        self.fo = open(filename, 'w')
    
    def push(self, string):
        self.fo.write(string)
        self.fo.write('\n')
    
    def flush(self):
        self.fo.flush()
            
    def __del__(self):
        self.fo.close()
    
    
class MaxCorrelationTrainer(object):    
    class FilteringType(Enum):
        Normalization = 0
        Domination = 1
    
    class CombiningType(Enum):
        Weighing = 0
        MNK = 1
    
    initial_single_functional = -np.inf
    best_functional_msg_template = 'Best {} ({}): {: .3f}'
    epsilon = 1e-4
    
    def __init__(self, voting_quality_threshold = 1e-3,\
                comparision_threshold = 1-1e-2,\
                filtering_type = FilteringType.Normalization,\
                combining_type = CombiningType.Weighing,\
                skip_selection = False, logger = PrintLogger()):
        self.voting_quality_threshold = voting_quality_threshold
        self.comparision_threshold = comparision_threshold
        self.filtering_type = filtering_type
        self.combining_type = combining_type
        self.enable_selection = not skip_selection
        self.logger = logger
        
        self.n_features = None
        self.history = []
        self.dominating_combinations = None
        self.classifiers = []
        
        self.best_functional = 0.0
        self.initial_single_functional = 0.0
        
    def get_inspector(self, sample, subset):
        return inspector.MaxCorrelationInspector(sample, subset)
        #pass
    
    def initial_combinations_functional(self, best_single):
        return best_single
        #pass
    
    def classifier_multiplier(self, functional):
        return 1 / (1 - np.square(functional))
        #pass
    
    def is_functional_better(self, old_functional, new_functional):
        return new_functional > old_functional
        #pass
    
    def is_functional_not_worse(self, old_functional, new_functional, threshold):
        return self.is_functional_better(old_functional * (1 - threshold), new_functional)
        #pass
    
    def get_resulting_weights(self):
        if self.n_features == None: return []
        res_weights = np.zeros(self.n_features)
        for clf in self.classifiers:
            res_weights += clf.weights * clf.multiplier
        return res_weights
    
    def __str__(self):
        return '; '.join(map(lambda v: '{: .3f}'.format(v),\
                             self.get_resulting_weights()))
    
    def log_func(self, idx, functional, single=False):
        descr = inspector.MaxCorrelationInspector.single_functional_description\
               if single else\
               inspector.MaxCorrelationInspector.complex_functional_description 
        self.logger.push(self.best_functional_msg_template.\
                         format(descr, idx, functional))

        
    def train(self, sample): # sample is X, y tuple
        logger, log_func = self.logger, self.log_func
        n_objects, n_features = sample.X.shape
        self.n_features = n_features
        pairs = [[] for x in xrange(n_features)]
        
        best_combination = None
        best_weights = None

        combinations = []
        # use all the features w/o selection        
        features = range(n_features)
                
        self.best_functional = self.initial_single_functional
        
        for feature in features:
            subset = [feature]
            combinations.append(subset)
            tested = self.get_inspector(sample, subset)
            tested.check()
            
            functional = tested.functional
            self.history.append(tested)
            
            if self.enable_selection and functional > self.best_functional:
                self.best_functional = functional
                best_combination = subset
                best_weights = tested.weights
                    
        if self.enable_selection:
            log_func(1, self.best_functional, single=True)
            best_functional = self.initial_combinations_functional(self.best_functional)
            
            # create pair map
            for pair in ([x, y] for x in xrange(n_features)\
                         for y in xrange(x+1, n_features)):
                tested = self.get_inspector(sample, pair)
                if tested.check():
                    pairs[pair[0]].append(pair[1])
                    
            #logger.push('found pairs: {}'.format(\
            #            {x:pair for (x,pair) in enumerate(pairs)}))
            
            # add list of combinations from the pair map
            for f_idx in xrange(1, n_features):
                best_prev_func = self.best_functional
                best_curr_func = self.initial_single_functional
                new_combinationations = []
                
                #print 'iteration =', f_idx, ' combinations =', combinations
                #logger.push('iteration = {}, combinations:\n[{}]'.\
                #            format(f_idx, '\n'.join(map(str, combinations))))
                
                for combo in combinations:
                    last = combo[f_idx - 1]
                    for fpair in pairs[last]:
                        subset = combo + [fpair]
                        tested = self.get_inspector(sample, subset)
                        if not tested.check():
                                continue
                            
                        functional = tested.functional
                        if not functional > best_prev_func * self.comparision_threshold:
                            continue
                        
                        #print 'combo=', subset, 'func=', functional    
                            
                        new_combinationations.append(subset)
                        self.history.append(tested)
                        if functional > best_curr_func:
                            best_curr_func = functional
                        if functional > self.best_functional * self.comparision_threshold:
                            self.best_functional = functional
                            best_combination = subset
                            best_weights = tested.weights
                if len(new_combinationations) <= 1: break
                combinations = new_combinationations
                log_func(f_idx, best_curr_func)
            # training results
            log_func('_', self.best_functional)
            logger.push('Best combination: ' + '; '.join(map(str, best_combination)))
            logger.flush()
            logger.push('Weights: ' + '; '.join(map(str, best_weights)))
            logger.flush()
        # show must go on
        high_resulted_combinations = [] # contains complex classifier
        logger.push('All combinations: ')
        for spector in self.history:
            print best_functional, spector.functional
            if self.is_functional_not_worse(best_functional, spector.functional,\
                                           self.voting_quality_threshold):
                weights = spector.weights
                weights_repr = ('{}({})'.format(i, w) for (i, w) in\
                                zip(spector.clf.feature_subset, weights))
                logger.push('{}: '.format(spector.functional) +\
                            '; '.join(weights_repr))
                high_resulted_combinations.append(classifier.ComplexClassifier(\
                    np.maximum(weights, 0), multiplier=spector.functional,\
                    feature_subset=spector.clf.feature_subset))
        logger.flush()
        
        # TODO: todo is there
        exclude = np.zeros((len(high_resulted_combinations)), dtype=bool)
        self.dominating_combinations = []
        for idx, hrcombo in enumerate(high_resulted_combinations):
            if not exclude[idx]:
                self.dominating_combinations.append(hrcombo)

    # TODO: ComplexClassifier weights sparse supports
    def forecast(self, train_sample, test_sample, all_results=True):
        logger, log_func = self.logger, self.log_func
        if self.dominating_combinations is None or\
        self.dominating_combinations == []:
            raise Exception # method hadn't trained jet
        res = np.zeros((test_sample.size))
        res_accepted = np.zeros((test_sample.size), dtype=bool)
        norms = np.zeros((test_sample.size))
        
        dominating_results = np.zeros((test_sample.size,\
                                       len(self.dominating_combinations)))
        
        for cidx, cclf in enumerate(self.dominating_combinations):
            #_, feature_subset = np.nonzero(cclf.weights > 0)
            #weights = cclf.weights[feature_subset]         
            feature_subset, weights = cclf.feature_subset, cclf.weights
            train_subset, test_subset = map(lambda sample:\
                np.nonzero(~np.isnan(sample.X[:,feature_subset].any(axis=1)))[0],\
                (train_sample, test_sample))

            nclf = classifier.ComplexClassifier(weights, multiplier=1,\
                                                feature_subset=feature_subset)
            nclf.set_classifier(classifier.Classifier(\
                train_sample, feature_subset, train_subset))

            result = nclf.classify(test_sample.X[test_subset,:])
            result = np.nan_to_num(result)
            
            dominating_results[:,cidx] = result

            res[test_subset] += result * cclf.multiplier
            norms[test_subset] += cclf.multiplier
            res_accepted[test_subset] = True
        
        if all_results: # other stats will be estimated by user
            return dominating_results
        
        # estimate stats
        class_errors = np.zeros(2)
        counts = [0, 0]
        
        rejects = np.sum(~res_accepted)
        if rejects == test_sample.size:
            return None
        
        epsilon = self.epsilon
        res[(norms > epsilon) & res_accepted] /= norms[(norms > epsilon) & res_accepted]
        y_test_predicted = np.double(res > 0.5)
        y_test = test_sample.y
        
        error = np.mean((y_test_predicted != y_test)[res_accepted])
        for class_ in [0, 1]:
            class_errors[class_] = np.sum(\
                ((y_test == class_) & (y_test_predicted != class_))[res_accepted])
        
        [[var_result, cov], [_, var_C]] = np.cov(\
            y_test_predicted[res_accepted], y_test[res_accepted])
        deviation = np.square((y_test-y_test_predicted)[res_accepted]).sum()
        
        stats = Struct(error=error, class_errors=class_errors, cov=cov,\
                       deviation=deviation, var_result=var_result, var_C=var_C)
        
        return res, stats
