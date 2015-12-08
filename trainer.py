import numpy as np
from enum import Enum
import gc
import itertools
from IPython.parallel import Client
from multiprocessing.pool import ThreadPool 

import classifier, inspector, storage


def gc_collect():
    import gc
    gc.collect()
    

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
        return self

    def flush(self):
        return self    
    
class PrintLogger(object):
    def __init__(self):
        import sys
        self.fo = sys.stdout
    
    def push(self, string):        
        self.fo.write(string)
        self.fo.write('\n')
        return self
    
    def flush(self):
        self.fo.flush()
        return self

        
class FileLogger(object):
    def __init__(self, filename):
        self.fo = open(filename, 'w')
    
    def push(self, string):
        self.fo.write(string)
        self.fo.write('\n')
        return self
    
    def flush(self):
        self.fo.flush()
        return self
            
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
    
    def __init__(self, voting_quality_threshold = 1e-3,
                comparision_threshold = 1-1e-2,
                filtering_type = FilteringType.Normalization,
                combining_type = CombiningType.Weighing,
                skip_selection = False, logger = PrintLogger(),
                parallel_profile = None,
                iterable_map = True):
        self.voting_quality_threshold = voting_quality_threshold
        self.comparision_threshold = comparision_threshold
        self.filtering_type = filtering_type
        self.combining_type = combining_type
        self.enable_selection = not skip_selection
        self.logger = logger
        
        self.n_features = None
        #self.history = []
        self.noncollapsed_combinations = storage.TreeStorage(data_handled=True)
        self.dominating_combinations = None
        self.classifiers = []
        
        self.best_functional = 0.0
        self.initial_single_functional = 0.0
        self.set_parallel_profile(parallel_profile)
        self.iterable_map = iterable_map

    @staticmethod
    def get_inspector(sample, subset):
        return inspector.MaxCorrelationInspector(sample, subset)

    @staticmethod
    def initial_combinations_functional(best_single):
        return best_single

    @staticmethod
    def classifier_multiplier(functional):
        return 1 / (1 - np.square(functional))

    @staticmethod
    def is_functional_better(old_functional, new_functional):
        return new_functional > old_functional

    @staticmethod
    def is_functional_not_worse(old_functional, new_functional, threshold):
        return MaxCorrelationTrainer.is_functional_better(
            old_functional * (1 - threshold), new_functional)
    
    def set_parallel_profile(self, parallel_profile=None):
        self.parallel_profile = parallel_profile
        if parallel_profile is None:
            pass
        elif str.startswith(parallel_profile, 'threads-'):
            from multiprocessing.pool import ThreadPool 
            self.n_threads = int(parallel_profile[len('threads-'):])
            self.pool = ThreadPool(processes=self.n_threads)
            self.pool._maxtasksperchild = 10**5
            self.logger.push('Running parallel in {} threads'.format(self.n_threads))
        else:
            from IPython.parallel import Client
            self.rc = Client(profile=parallel_profile)
            dv = self.rc.direct_view()
            self.logger.push('Running parallel on cluster on {} cores'.format(len(dv)))
    
    def get_pmap(self):
        if self.parallel_profile is None:
            return itertools.imap if self.iterable_map else map
        if str.startswith(self.parallel_profile, 'threads-'):
            return self.pool.imap if self.iterable_map else self.pool.map
        else:
            lbv = self.rc.load_balanced_view()
            return lbv.imap if self.iterable_map else lbv.map_sync
    
    def garbage_collect(self):
        if self.parallel_profile is None:
            gc_collect()
        elif str.startswith(self.parallel_profile, 'threads-'):
            gc_collect()
        else:
            self.rc.dirrect_view().apply(gc_collect)


    def get_resulting_weights(self):
        if self.n_features == None: return []
        res_weights = np.zeros(self.n_features)
        for clf in self.classifiers:
            res_weights += clf.weights * clf.multiplier
        return res_weights
    
    def __str__(self):
        return '; '.join(map(lambda v: '{: .3f}'.format(v),
                             self.get_resulting_weights()))
    
    def log_func(self, idx, functional, single=False):
        descr = inspector.MaxCorrelationInspector.single_functional_description\
               if single else\
               inspector.MaxCorrelationInspector.complex_functional_description 
        self.logger.push(self.best_functional_msg_template.format(descr, idx, functional))
        self.logger.flush()

    def train(self, sample, force_garbage_collector=True):
        logger, log_func = self.logger, self.log_func
        n_objects, n_features = sample.X.shape
        self.n_features = n_features
        pairs = [[] for x in xrange(n_features)]
        
        best_combination = None
        best_weights = None

        combinations = storage.TreeStorage(data_handled=False)
        # use all the features w/o selection        
        features = range(n_features)
                
        self.best_functional = self.initial_single_functional

        def hist_push(inspctr):
            self.noncollapsed_combinations.add_node(
                inspctr.feature_subset,
                data=(inspctr.functional, inspctr.weights))

        for feature in features:
            subset = [feature]
            combinations.append(subset)
            tested = self.get_inspector(sample, subset)
            tested.check()
            
            functional = tested.functional
            hist_push(tested)

            if self.enable_selection and functional > self.best_functional:
                self.best_functional = functional
                best_combination = subset
                best_weights = tested.weights
                    
        if self.enable_selection:
            log_func(1, self.best_functional, single=True)
            best_functional = self.initial_combinations_functional(self.best_functional)
            
            pmap = self.get_pmap()
            
            for first in xrange(n_features):
                def pair_check(second):
                    if self.get_inspector(sample, [first, second]).check():
                        return second
                    return None
                second_check = pmap(pair_check, xrange(first+1, n_features))
                pairs[first] = filter(None, second_check)
            
            def combo_pair_iter(combos):
                for combo in combos:
                    last = combo[-1]
                    for second in pairs[last]:
                        yield combo + [second]
            
            def test_check(combo):
                tested = self.get_inspector(sample, combo)
                if not tested.check():
                    return None
                if not tested.functional > best_prev_func * self.comparision_threshold:
                    return None
                return Struct(feature_subset=tested.feature_subset,
                              functional=tested.functional,
                              weights=tested.weights)

            for iter_idx in xrange(1, n_features):
                best_prev_func = self.best_functional
                best_curr_func = self.initial_single_functional
                new_combinations = storage.TreeStorage(data_handled=False)
                if force_garbage_collector: self.garbage_collect()

                testeds = pmap(test_check, combo_pair_iter(combinations))
                for (combo, tested) in itertools.izip(combo_pair_iter(combinations),
                                                      testeds):
                    if tested is None: continue
                    hist_push(tested)
                    new_combinations.append(combo)
                    if tested.functional > best_curr_func:
                        best_curr_func = tested.functional
                    if tested.functional > self.best_functional:
                        self.best_functional = functional
                        best_combination = combo
                        best_weights = tested.weights
                if len(new_combinations) <= 1: break
                del combinations
                combinations = new_combinations
                log_func(iter_idx+1, best_curr_func)

            # training results
            log_func('_', self.best_functional)
            logger.push('Best combination: ' + '; '.join(map(str, best_combination)))
            logger.push('Weights: ' + '; '.join(map(str, best_weights))).flush()
                
        ##debug
        return self.noncollapsed_combinations
        ##debug
 
        high_resulted_combinations = []
        logger.push('All combinations: ')                
        for (feature_subset, (functional, weights))\
            in self.noncollapsed_combinations.iteritems():
            if self.is_functional_not_worse(self.best_functional, functional,
                                            self.voting_quality_threshold):
                weights_repr = ('{}({})'.format(i, w) for (i, w) in
                                zip(feature_subset, weights))
                logger.push('{}: '.format(functional) +
                            '; '.join(weights_repr))
                high_resulted_combinations.append(classifier.ComplexClassifier(
                    np.maximum(weights, 0), multiplier=functional,
                    feature_subset=feature_subset
                ))
        logger.flush()
        
        # TODO: todo is there
        exclude = np.zeros((len(high_resulted_combinations)), dtype=bool)
        self.dominating_combinations = []
        for idx, hrcombo in enumerate(high_resulted_combinations):
            if not exclude[idx]:
                self.dominating_combinations.append(hrcombo)

    def forecast(self, train_sample, test_sample, all_results=True):
        logger, log_func = self.logger, self.log_func
        if self.dominating_combinations is None or\
        self.dominating_combinations == []:
            raise Exception # method hadn't trained jet
        res = np.zeros((test_sample.size))
        res_accepted = np.zeros((test_sample.size), dtype=bool)
        norms = np.zeros((test_sample.size))
        
        dominating_results = np.zeros((
            test_sample.size, len(self.dominating_combinations)))
        
        for cidx, cclf in enumerate(self.dominating_combinations):
            #_, feature_subset = np.nonzero(cclf.weights > 0)
            #weights = cclf.weights[feature_subset]         
            feature_subset, weights = cclf.feature_subset, cclf.weights
            train_subset, test_subset = map(
                lambda sample: np.nonzero(~np.isnan(sample.X[:,feature_subset].any(axis=1)))[0],
                (train_sample, test_sample))

            nclf = classifier.ComplexClassifier(weights, multiplier=1,
                                                feature_subset=feature_subset)
            nclf.set_classifier(classifier.Classifier(train_sample, feature_subset, train_subset))

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
            class_errors[class_] = np.sum(((y_test == class_) & (y_test_predicted != class_))[res_accepted])
        
        [[var_result, cov], [_, var_C]] = np.cov(y_test_predicted[res_accepted], y_test[res_accepted])
        deviation = np.square((y_test-y_test_predicted)[res_accepted]).sum()
        
        stats = Struct(error=error, class_errors=class_errors, cov=cov,
                       deviation=deviation, var_result=var_result, var_C=var_C)
        
        return res, stats
