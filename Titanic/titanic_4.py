import pandas as pd
import numpy as np
import re
import sklearn
import xgboost as xgb
import seaborn as sns
import matplotlib.pyplot as plt
import warnings
import plotly.offline as py
import plotly.graph_objs as go
import plotly.tools as tls

from sklearn.ensemble import (RandomForestClassifier, AdaBoostClassifier,
                              GradientBoostingClassifier, ExtraTreesClassifier)
from sklearn.svm import SVC
from sklearn.cross_validation import KFold

py.init_notebook_mode(connected=True)
warnings.filterwarnings('ignore')

# Going to use these 5 base models for the stacking

# Feature Exploration, Engineering and Cleaning
# Load in the train and test datasets
train = pd.read_csv('./data/train.csv')
test = pd.read_csv('./data/test.csv')

# Store our passenger ID for easy access
PassengerId = test['PassengerId']

full_data = [train, test]

train['Name_length'] = train['Name'].apply(len)
test['Name_length'] = test['Name'].apply(len)
train['Has_Cabin'] = train['Cabin'].apply(lambda x: 0 if type(x) == float else 1)
test['Has_Cabin'] = test['Cabin'].apply(lambda x: 0 if type(x) == float else 1)

for dataset in full_data:
    dataset['FamilySize'] = dataset['SibSp'] + dataset['Parch'] + 1

for dataset in full_data:
    dataset['IsAlone'] = 0
    dataset.loc[dataset['FamilySize'] == 1, 'IsAlone'] = 1

for dataset in full_data:
    dataset['Embarked'] = dataset['Embarked'].fillna('S')

for dataset in full_data:
    dataset['Fare'] = dataset['Fare'].fillna(train['Fare'].median())

train['CategoricalFare'] = pd.qcut(train['Fare'], 4)

for dataset in full_data:
    age_avg = dataset['Age'].mean()
    age_std = dataset['Age'].std()
    age_null_count = dataset['Age'].isnull().sum()
    age_null_random_list = np.random.randint(age_avg - age_std, age_avg + age_std, size=age_null_count)
    dataset['Age'][np.isnan(dataset['Age'])] = age_null_random_list
    dataset['Age'] = dataset['Age'].astype(int)

train['CategoricalAge'] = pd.cut(train['Age'], 5)


def get_title(name):
    title_search = re.search(' ([A-Za-z]+)\.', name)
    if title_search:
        return title_search.group(1)
    return ""


for dataset in full_data:
    dataset['Title'] = dataset['Name'].apply(get_title)
    dataset['Title'] = dataset['Title'].replace(['Lady', 'Countess', 'Capt', 'Col', 'Don', 'Dr',
                                                 'Major', 'Rev', 'Sir', 'Jonkheer', 'Dona'], 'Rare')
    dataset['Title'] = dataset['Title'].replace('Mlle', 'Miss')
    dataset['Title'] = dataset['Title'].replace('Ms', 'Miss')
    dataset['Title'] = dataset['Title'].replace('Mme', 'Mrs')

for dataset in full_data:
    dataset['Sex'] = dataset['Sex'].map({'female': 0, 'male': 1}).astype(int)

    title_mapping = {'Mr': 1, 'Miss': 2, 'Mrs': 3, 'Master': 4, 'Rare': 5}
    dataset['Title'] = dataset['Title'].map(title_mapping)
    dataset['Title'] = dataset['Title'].fillna(0)

    dataset['Embarked'] = dataset['Embarked'].map({'S': 0, 'C': 1, 'Q': 2}).astype(int)

    dataset.loc[dataset['Fare'] <= 7.91, 'Fare'] = 0
    dataset.loc[(dataset['Fare'] > 7.91) & (dataset['Fare'] <= 14.454), 'Fare'] = 1
    dataset.loc[(dataset['Fare'] > 14.454) & (dataset['Fare'] <= 31), 'Fare'] = 2
    dataset.loc[dataset['Fare'] > 31, 'Fare'] = 3
    dataset['Fare'] = dataset['Fare'].astype(int)

    dataset.loc[dataset['Age'] <= 16, 'Age'] = 0
    dataset.loc[(dataset['Age'] > 16) & (dataset['Age'] <= 32), 'Age'] = 1
    dataset.loc[(dataset['Age'] > 32) & (dataset['Age'] <= 48), 'Age'] = 2
    dataset.loc[(dataset['Age'] > 48) & (dataset['Age'] <= 64), 'Age'] = 3
    dataset.loc[dataset['Age'] > 64, 'Age'] = 4

drop_elements = ['PassengerId', 'Name', 'Ticket', 'Cabin', 'SibSp']
train = train.drop(drop_elements, axis=1)
train = train.drop(['CategoricalAge', 'CategoricalFare'], axis=1)
test = test.drop(drop_elements, axis=1)

# Pearson Correlation Heatmap
colormap = plt.cm.RdBu
plt.figure(figsize=(14, 12))
plt.title('Pearson Correlation of Features', y=1.05, size=15)
sns.heatmap(train.astype(float).corr(), linewidths=0.1, vmax=1.0,
            square=True, cmap=colormap, linecolor='white', annot=True)
# plt.show()

# Ensembling & Stacking models
# Some useful parameters which will come in handy later on
ntrain = train.shape[0]
ntest = test.shape[0]
SEED = 0  # for reproducibility
NFOLDS = 5  # set folds for out-of-fold prediction
kf = KFold(ntrain, n_folds=NFOLDS, random_state=SEED)


# Class to extend the Sklearn classifier
class SklearnHelper(object):
    def __init__(self, clf, seed=0, params=None):
        params['random_state'] = seed
        self.clf = clf(**params)

    def train(self, x_train, y_train):
        self.clf.fit(x_train, y_train)

    def predict(self, x):
        return self.clf.predict(x)

    def fit(self, x, y):
        return self.clf.fit(x, y)

    def feature_importances(self, x, y):
        print(self.clf.fit(x, y).feature_importances_)

# Class to extend XGboost classifer


def get_oof(clf, x_train, y_train, x_test):
    oof_train = np.zeros((ntrain,))
    oof_test = np.zeros((ntest,))
    oof_test_skf = np.empty((NFOLDS, ntest))

    for i, (train_index, test_index) in enumerate(kf):
        x_tr = x_train[train_index]
        y_tr = y_train[train_index]
        x_te = x_train[test_index]

        clf.train(x_tr, y_tr)

        oof_train[test_index] = clf.predict(x_te)
        oof_test_skf[i, :] = clf.predict(x_test)

    oof_test[:] = oof_test_skf.mean(axis=0)
    return oof_train.reshape(-1, 1), oof_test.reshape(-1, 1)


# Put in our parameters for said classifiers
# Random Forest parameters
rf_params = {'n_jobs': 1,
             'n_estimators': 500,
             # 'max_features': 0.2,
             'max_depth': 6,
             'min_samples_leaf': 2,
             'max_features': 'sqrt',
             'verbose': 0}

# Extra Trees Parameters
et_params = {'n_jobs': -1,
             'n_estimators': 500,
             # 'max_features': 0.5,
             'max_depth': 8,
             'min_samples_leaf': 2,
             'verbose': 0}

# AdaBoost parameters
ada_params = {'n_estimators': 500,
              'learning_rate': 0.75}

# Gradient Boosting parameters
gb_params = {'n_estimators': 500,
             # 'max_features': 0.2,
             'max_depth': 5,
             'min_samples_leaf': 2,
             'verbose': 0}

# Support Vector Classifier parameters
svc_params = {'kernel': 'linear',
              'C': 0.025}

rf = SklearnHelper(clf=RandomForestClassifier, seed=SEED, params=rf_params)
et = SklearnHelper(clf=ExtraTreesClassifier, seed=SEED, params=et_params)
ada = SklearnHelper(clf=AdaBoostClassifier, seed=SEED, params=ada_params)
gb = SklearnHelper(clf=GradientBoostingClassifier, seed=SEED, params=gb_params)
svc = SklearnHelper(clf=SVC, seed=SEED, params=svc_params)

y_train = train['Survived'].ravel()
train = train.drop(['Survived'], axis=1)
x_train = train.values
x_test = test.values

rf_oof_train, rf_oof_test = get_oof(rf, x_train, y_train, x_test)
et_oof_train, et_oof_test = get_oof(et, x_train, y_train, x_test)
ada_oof_train, ada_oof_test = get_oof(ada, x_train, y_train, x_test)
gb_oof_train, gb_oof_test = get_oof(gb, x_train, y_train, x_test)
svc_oof_train, svc_oof_test = get_oof(svc, x_train, y_train, x_test)
print('Training is complete')

rf_feature = rf.feature_importances(x_train, y_train)
et_feature = et.feature_importances(x_train, y_train)
ada_feature = ada.feature_importances(x_train, y_train)
gb_feature = gb.feature_importances(x_train, y_train)

rf_features = [0.11000162, 0.23903925, 0.03419423, 0.01928891, 0.04891337, 0.02226342,
               0.11185771, 0.06807714, 0.07123443, 0.01107738, 0.26405254]
et_features = [0.12217183, 0.3814256, 0.02974297, 0.01645624, 0.05601167, 0.02974112,
               0.04646209, 0.08151918, 0.04512293, 0.02214522, 0.16920115]
ada_features = [0.034, 0.014, 0.016, 0.062, 0.042, 0.01, 0.69, 0.014, 0.046, 0.008, 0.064]
gb_features = [0.07818988, 0.04311359, 0.10444991, 0.02913075, 0.09317813, 0.05291786,
               0.39493944, 0.0173372, 0.07274152, 0.02609468, 0.08790704]

cols = train.columns.values
feature_dataframe = pd.DataFrame({'features': cols,
                                  'Random Forest feature importances': rf_features,
                                  'Extra Trees feature importances': et_features,
                                  'AdaBoost feature importances': ada_features,
                                  'Gradient Boost feature importances': gb_features})

# Scatter plot
trace = go.Scatter(y=feature_dataframe['Random Forest feature importances'].values,
                   x=feature_dataframe['features'].values, mode='markers',
                   marker=dict(sizemode='diameter',
                               sizeref=1,
                               size=25,
                               # size = feature_dataframe['AdaBoost feature importances'].values,
                               # color = np.random.randn(500),
                               color=feature_dataframe['Random Forest feature importances'].values,
                               colorscale='Portland',
                               showscale=True),
                   text=feature_dataframe['features'].values)
data = [trace]

layout = go.Layout(autosize=True, title='Random Forest Feature Importance', hovermode='closest',
                   # xaxis=dict(title='Pop',
                   #            ticklen=5,
                   #            zeroline=False,
                   #            gridwidth=2),
                   yaxis=dict(title='Feature Importance',
                              ticklen=5,
                              gridwidth=2),
                   showlegend=False)
fig = go.Figure(data=data, layout=layout)
py.plot(fig, filename='scatter2010')

# Scatter plot
trace = go.Scatter(y=feature_dataframe['Extra Trees feature importances'].values,
                   x=feature_dataframe['features'].values,
                   mode='markers',
                   marker=dict(sizemode='diameter',
                               sizeref=1,
                               size=25,
                               # size=feature_dataframe['AdaBoost feature importances'].values,
                               # color=np.random.randn(500),
                               color=feature_dataframe['Extra Trees feature importances'].values,
                               colorscale='Portland',
                               showscale=True),
                   text=feature_dataframe['features'].values)
data = [trace]

layout = go.Layout(autosize=True, title='Extra Trees Feature Importance', hovermode='closest',
                   # xaxis= dict(title='Pop',
                   #             ticklen=5,
                   #             zeroline=False,
                   #             gridwidth=2),
                   yaxis=dict(title='Feature Importance',
                              ticklen=5,
                              gridwidth=2),
                   showlegend=False)
fig = go.Figure(data=data, layout=layout)
py.plot(fig, filename='scatter2010')

# Scatter plot
trace = go.Scatter(y=feature_dataframe['AdaBoost feature importances'].values,
                   x=feature_dataframe['features'].values,
                   mode='markers',
                   marker=dict(sizemode='diameter',
                               sizeref=1,
                               size=25,
                               # size= feature_dataframe['AdaBoost feature importances'].values,
                               # color = np.random.randn(500), #set color equal to a variable
                               color=feature_dataframe['AdaBoost feature importances'].values,
                               colorscale='Portland',
                               showscale=True),
                   text=feature_dataframe['features'].values)
data = [trace]

layout = go.Layout(autosize=True, title='AdaBoost Feature Importance', hovermode='closest',
                   # xaxis=dict(title='Pop',
                   #              ticklen=5,
                   #              zeroline=False,
                   #              gridwidth=2,),
                   yaxis=dict(title='Feature Importance',
                              ticklen=5,
                              gridwidth=2),
                   showlegend=False)
fig = go.Figure(data=data, layout=layout)
py.plot(fig, filename='scatter2010')

# Scatter plot
trace = go.Scatter(y=feature_dataframe['Gradient Boost feature importances'].values,
                   x=feature_dataframe['features'].values,
                   mode='markers',
                   marker=dict(sizemode='diameter',
                               sizeref=1,
                               size=25,
                               # size=feature_dataframe['AdaBoost feature importances'].values,
                               # color=np.random.randn(500),
                               color=feature_dataframe['Gradient Boost feature importances'].values,
                               colorscale='Portland',
                               showscale=True),
                   text=feature_dataframe['features'].values)
data = [trace]

layout = go.Layout(autosize=True, title='Gradient Boosting Feature Importance', hovermode='closest',
                   # xaxis=dict(title='Pop',
                   #            ticklen=5,
                   #            zeroline=False,
                   #            gridwidth=2),
                   yaxis=dict(title='Feature Importance',
                              ticklen=5,
                              gridwidth=2),
                   showlegend=False)
fig = go.Figure(data=data, layout=layout)
py.plot(fig, filename='scatter2010')

feature_dataframe['mean'] = feature_dataframe.mean(axis=1)
# print(feature_dataframe)

y = feature_dataframe['mean'].values
x = feature_dataframe['features'].values
data = [go.Bar(x=x, y=y, width=0.5, marker=dict(color=feature_dataframe['mean'].values,
                                                colorscale='Portland',
                                                showscale=True,
                                                reversescale=False),
               opacity=0.6)]
layout = go.Layout(autosize=True, title='Barplots of Mean Feature Importance', hovermode='closest',
                   # xaxis=dict(title='Pop',
                   #            ticklen=5,
                   #            zeroline=False,
                   #            gridwidth=2),
                   yaxis=dict(title='Feature Importance',
                              ticklen=5,
                              gridwidth=2),
                   showlegend=False)
fig = go.Figure(data=data, layout=layout)
py.plot(fig, filename='bar-direct-labels')

base_predictions_train = pd.DataFrame({'RandomForest': rf_oof_train.ravel(),
                                       'ExtraTrees': et_oof_train.ravel(),
                                       'AdaBoost': ada_oof_train.ravel(),
                                       'GradientBoost': gb_oof_train.ravel()})
# print(base_predictions_train)

data = [go.Heatmap(z=base_predictions_train.astype(float).corr().values,
                   x=base_predictions_train.columns.values,
                   y=base_predictions_train.columns.values,
                   colorscale='Viridis',
                   showscale=True,
                   reversescale=True)]
py.plot(data, filename='labelled-heatmap')

x_train = np.concatenate((et_oof_train, rf_oof_train, ada_oof_train, gb_oof_train, svc_oof_train), axis=1)
x_test = np.concatenate((et_oof_test, rf_oof_test, ada_oof_test, gb_oof_test, svc_oof_test), axis=1)

gbm = xgb.XGBClassifier(n_estimators=2000,
                        max_depth=4,
                        min_child_weight=2,
                        gamma=0.9,
                        subsample=0.8,
                        colsample_bytree=0.8,
                        objective='binary:logistic',
                        nthread=-1,
                        scale_pos_weight=1).fit(x_train, y_train)
predictions = gbm.predict(x_test)

StackingSubmission = pd.DataFrame({'PassengerId': PassengerId, 'Survived': predictions})
StackingSubmission.to_csv('./data/StackingSubmission.csv', index=False)
