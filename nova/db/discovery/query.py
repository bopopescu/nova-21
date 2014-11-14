# Copyright (c) 2011 X.commerce, a business unit of eBay Inc.
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Implementation of Discovery backend."""

import collections
import copy
import datetime
import functools
import sys
import threading
import time
import uuid

from oslo.config import cfg
from oslo.db import exception as db_exc
from oslo.db.sqlalchemy import session as db_session
from oslo.db.sqlalchemy import utils as sqlalchemyutils
from oslo.utils import excutils
from oslo.utils import timeutils
import six
from sqlalchemy import and_
from sqlalchemy import Boolean
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import or_
from sqlalchemy.orm import contains_eager
from sqlalchemy.orm import joinedload
from sqlalchemy.orm import joinedload_all
from sqlalchemy.orm import noload
from sqlalchemy.orm import undefer
from sqlalchemy.schema import Table
from sqlalchemy import sql
from sqlalchemy.sql.expression import asc
from sqlalchemy.sql.expression import desc
from sqlalchemy.sql import false
from sqlalchemy.sql import func
from sqlalchemy.sql import null
from sqlalchemy.sql import true
from sqlalchemy import String

from nova import block_device
from nova.compute import task_states
from nova.compute import vm_states
import nova.context
# from nova.db.sqlalchemy import models
from nova import exception
from nova.i18n import _, _LI
from nova.openstack.common import log as logging
from nova.openstack.common import uuidutils

try:
    from nova import quota
except:
    pass

# RIAK
import itertools
import traceback
import uuid
import pprint
import riak
import inspect
from inspect import getmembers
from sqlalchemy.util._collections import KeyedTuple
import netaddr
from sqlalchemy.sql.expression import BinaryExpression
from sqlalchemy.orm.evaluator import EvaluatorCompiler
from sqlalchemy.orm.collections import InstrumentedList
from nova.db.discovery import models
import pytz
try:
    from desimplifier import ObjectDesimplifier
    from desimplifier import find_table_name
except:
    pass

dbClient = riak.RiakClient(pb_port=8087, protocol='pbc')

class Selection:
    def __init__(self, model, attributes):
        self._model = model
        self._attributes = attributes

class Function:

    def collect_field(self, rows, field):
        if rows is None:
            rows = []
        if "." in field:
            field = field.split(".")[1]
        result = [ getattr(row, field) for row in rows]
        return result

    def count(self, rows):
        collected_field_values = self.collect_field(rows, self._field)
        return len(collected_field_values)

    def sum(self, rows):
        result = 0
        collected_field_values = self.collect_field(rows, self._field)
        try:
            result = sum(collected_field_values)
        except:
            pass
        return result



    def __init__(self, name, field):
        self._name = name
        if name == "count":
            self._function = self.count
        elif name == "sum":
            self._function = self.sum
        else:
            self._function = self.sum
        self._field = field


import re

class RiakModelQuery:

    _funcs = []
    _initial_models = []
    _models = []
    _criterions = []

    def __init__(self, *args, **kwargs):
        self._models = []
        self._criterions = []
        self._funcs = []

        base_model = None
        if kwargs.has_key("base_model"):
            base_model = kwargs.get("base_model")

        for arg in args:
            if "count" in str(arg) or "sum" in str(arg):
                function_name = re.sub("\(.*\)", "", str(arg))
                field_id = re.sub("\)", "", re.sub(".*\(", "", str(arg)))
                self._funcs += [Function(function_name, field_id)]
            elif self.find_table_name(arg) != "none":
                if hasattr(arg, "_sa_class_manager"):
                    self._models += [Selection(arg, "*")]
                elif hasattr(arg, "class_"):
                    self._models += [Selection(arg.class_, "*")]
                else:
                    pass
            elif isinstance(arg, Selection):
                self._models += [arg]
            elif isinstance(arg, Function):
                self._funcs += [arg]
            elif isinstance(arg, BinaryExpression):
                self._criterions += [arg]
            else:
                pass

        def unique(l):
            already_processed = set()
            result = []
            for selectable in l:
                if not selectable._model in already_processed:
                    already_processed.add(selectable._model)
                    result += [selectable]
            return result

        if len(self._models) == 0 and len(self._funcs) > 0:
            if base_model:
                self._models = [Selection(base_model, "*")]

        self._models = unique(self._models)

    def get_single_object(self, model, id):
            
        try:
            from desimplifier import ObjectDesimplifier
        except:
            pass

        if isinstance(id, int):
            
            object_desimplifier = ObjectDesimplifier()
            
            table_name = self.find_table_name(model)
            object_bucket = dbClient.bucket(table_name)

            key_as_string = "%d" % (id)
            value = object_bucket.get(key_as_string)
            
            try:
                return object_desimplifier.desimplify(value.data)
            except Exception as e:
                traceback.print_exc()
                return None
        else:
            return None

    def get_objects(self, model):

        table_name = self.find_table_name(model)
            
        key_index_bucket = dbClient.bucket("key_index")
        fetched = key_index_bucket.get(table_name)
        keys = fetched.data

        result = []
        if keys != None:
            for key in keys:
                try:
                    key_as_string = "%d" % (key)
                    
                    model_object = self.get_single_object(model, key)       

                    result = result + [model_object]
                except Exception as ex:
                    print("problem with key: %s" %(key))
                    traceback.print_exc()
                    pass
                    
        return result


    def find_table_name(self, model):

        """This function return the name of the given model as a String. If the
        model cannot be identified, it returns "none".
        :param model: a model object candidate
        :return: the table name or "none" if the object cannot be identified
        """

        if hasattr(model, "__tablename__"):
            return model.__tablename__

        if hasattr(model, "table"):
            return model.table.name

        if hasattr(model, "class_"):
            return model.class_.__tablename__

        if hasattr(model, "clauses"):
            for clause in model.clauses:
                return self.find_table_name(clause)

        return "none"

    def construct_rows(self):

        """This function constructs the rows that corresponds to the current query.
        :return: a list of row, according to sqlalchemy expectation
        """

        def load_relationship(object):

            """ Check if the object contains relationships. If so, it loads related objects according to sqlalchemy
            expectation.

            :param object: object that will be checked
            :return:the given object, loaded with its potential relationships
            """

            attributes = []
            if hasattr(object, "_sa_class_manager"):
                attributes = object._sa_class_manager

            for attribute in attributes:
                is_relationship_field = False

                local_join_field = None
                remote_join_field = None
                remote_join_class = None

                # relationship_type has several values:
                #   * 0 (Many -> one)
                #   * 1 (One -> Many)
                relationship_type = None

                try:
                    for fk in object.metadata._fk_memos:

                        ########################################################
                        # Many -> one relationship
                        ########################################################
                        if fk[0] == attribute:
                            if "%s." % (attribute) in str(attributes[attribute].expression.right):
                                local_join_field = str(attributes[attribute].expression.left).split(".")[-1]
                                remote_join_class = attribute
                                remote_join_field = str(attributes[attribute].expression.right).split(".")[-1]
                                pass
                            else:
                                local_join_field = str(attributes[attribute].expression.right).split(".")[-1]
                                remote_join_class = attribute
                                remote_join_field = str(attributes[attribute].expression.left).split(".")[-1]
                                pass

                            relationship_type = 0
                            is_relationship_field = True

                        ########################################################
                        # One -> Many relationship
                        ########################################################
                        elif fk[0] == object.__tablename__:

                            if hasattr(attributes[attribute].expression, "right"):
                                if "%s." % (attribute) not in str(attributes[attribute].expression.right):
                                    local_join_field = str(attributes[attribute].expression.left).split(".")[-1]
                                    remote_join_class = str(attributes[attribute].expression.right).split(".")[-2]
                                    remote_join_field = str(attributes[attribute].expression.right).split(".")[-1]
                                    pass
                                else:
                                    local_join_field = str(attributes[attribute].expression.right).split(".")[-1]
                                    remote_join_class = str(attributes[attribute].expression.left).split(".")[-2]
                                    remote_join_field = str(attributes[attribute].expression.left).split(".")[-1]
                                    pass
                            else:
                                return object
                                pass
                            relationship_type = 1
                            is_relationship_field = True
                except:
                    is_relationship_field = False

                if is_relationship_field:
                    if relationship_type == 0:
                        entity_class = globals()[attribute.capitalize()]
                    elif relationship_type == 1:
                        entity_class = globals()[remote_join_class.capitalize()]

                    related_objects = []
                    objects = RiakModelQuery(entity_class).get_objects(entity_class)
                    for remote_object in objects:
                        if getattr(object, local_join_field) == getattr(remote_object, remote_join_field):
                            if (relationship_type == 0):
                                setattr(object, attribute, remote_object)
                            elif relationship_type == 1:
                                related_objects += [remote_object]

                    if relationship_type == 1:
                        setattr(object, attribute, related_objects)

            return object

        def extract_sub_row(row, selectables):

            """Adapt a row result to the expectation of sqlalchemy.
            :param row: a list of python objects
            :param selectables: a list entity class
            :return: the response follows what is required by sqlalchemy (if len(model)==1, a single object is fine, in
            the other case, a KeyTuple where each sub object is associated with it's entity name
            """

            if len(selectables) > 1:

                labels = []

                for selectable in selectables:
                    labels += [self.find_table_name(selectable._model).capitalize()]

                product = []
                for label in labels:
                    product = [getattr(row, label)] + product

                # Updating Foreign Keys of objects that are in the row
                for label in labels:
                    current_object = getattr(row, label)
                    metadata = current_object.metadata
                    if metadata and hasattr(metadata, "_fk_memos"):
                        for fk_name in metadata._fk_memos:
                            fks = metadata._fk_memos[fk_name]
                            for fk in fks:
                                local_field_name = fk.column._label
                                remote_table_name = fk._colspec.split(".")[-2].capitalize()
                                remote_field_name = fk._colspec.split(".")[-1]

                                try:
                                    remote_object = getattr(row, remote_table_name)
                                    remote_field_value = getattr(remote_object, remote_field_name)
                                    setattr(current_object, local_field_name, remote_field_value)
                                except:
                                    pass

                # Updating fields that are setted to None and that have default values
                for label in labels:
                    current_object = getattr(row, label)
                    for field in current_object._sa_class_manager:
                        instance_state = current_object._sa_instance_state
                        field_value = getattr(current_object, field)
                        if field_value is None:
                            try:
                                field_column = instance_state.mapper._props[field].columns[0]
                                field_default_value = field_column.default.arg
                                setattr(current_object, field, field_default_value)
                                print(field_default_value)
                            except:
                                pass
                        print(field)

                return KeyedTuple(product, labels=labels)
            else:
                model_name = self.find_table_name(selectables[0]._model).capitalize()
                return getattr(row, model_name)


        labels = []
        columns = set([])
        rows = []

        # get the fields of the join result
        for selectable in self._models:
            labels += [self.find_table_name(selectable._model).capitalize()]

            if selectable._attributes == "*":
                try:
                    selected_attributes = selectable._model._sa_class_manager
                except:
                    # print "selectable._model -> %s" % (selectable._model)
                    selected_attributes = selectable._model.class_._sa_class_manager
                    pass
            else:
                selected_attributes = [selectable._attributes]

            for field in selected_attributes:
                try:
                    attribute = selectable._model._sa_class_manager[field].__str__()
                except:
                    attribute = selectable._model.class_._sa_class_manager[field].__str__()
                    pass

                columns.add(attribute)

        # construct the cartesian product
        list_results = []
        for selectable in self._models:
            list_results += [map(load_relationship, self.get_objects(selectable._model))]
            # list_results += [self.get_objects(model)]

        # construct the cartesian product
        cartesian_product = []
        for element in itertools.product(*list_results):
            cartesian_product += [element]

        # filter elements of the cartesian product
        for product in cartesian_product:
            if len(product) > 0:
                row = KeyedTuple(product, labels=labels)
                all_criterions_satisfied = True

                for criterion in self._criterions:
                    if not self.evaluate_criterion(criterion, row):
                        all_criterions_satisfied = False
                if all_criterions_satisfied and not row in rows:
                    # rows += [extract_sub_row(row, self._initial_models)]
                    rows += [extract_sub_row(row, self._models)]

        # Now we check if this query contains functions such as count(*) or sum(*). If so, we compute their value.
        if len(self._funcs) > 0:
            row = []
            labels = []
            i = 0
            for func in self._funcs:
                labels += [i]
                row += [func._function(rows)]
                i += 1
            return [KeyedTuple(row, labels=labels)]
        else:
            return rows


    def evaluate_criterion(self, criterion, value):

        def uncapitalize(str):
            return s[:1].lower() + s[1:] if s else ''

        def getattr_rec(obj, attr, otherwise=None):
            """ A reccursive getattr function.

            :param obj: the object that will be use to perform the search
            :param attr: the searched attribute
            :param otherwise: value returned in case attr was not found
            :return:
            """
            try:
                if not "." in attr:
                    return getattr(obj, attr)
                else:
                    current_key = attr[:attr.index(".")]
                    next_key = attr[attr.index(".") + 1:]
                    if hasattr(obj, current_key):
                        current_object = getattr(obj, current_key)
                    elif hasattr(obj, current_key.capitalize()):
                        current_object = getattr(obj, current_key.capitalize())
                    elif hasattr(obj, uncapitalize(current_key)):
                        current_object = getattr(obj, uncapitalize(current_key))
                    else:                        
                        current_object = getattr(obj, current_key)

                    return getattr_rec(current_object, next_key, otherwise)
            except AttributeError:
                    return otherwise

        criterion_str = criterion.__str__()

        if "=" in criterion_str:
            def comparator (a, b):
                if a is None or b is None:
                    return False
                return "%s" %(a) == "%s" %(b)
            op = "="

        if "IS" in criterion_str:
            def comparator (a, b):
                if a is None or b is None:
                    if a is None and b is None:
                        return True
                    else:
                        return False
                return a is b
            op = "IS"

        if "!=" in criterion_str:
            def comparator (a, b):
                if a is None or b is None:
                    return False
                return a is not b
            op = "!="

        if "<" in criterion_str:
            def comparator (a, b):
                if a is None or b is None:
                    return False
                return a < b
            op = "<"

        if ">" in criterion_str:
            def comparator (a, b):
                if a is None or b is None:
                    return False
                return a > b
            op = ">"

        if "IN" in criterion_str:
            def comparator (a, b):
                if a is None or b is None:
                    return False
                return a == b
            op = "IN"

        split = criterion_str.split(op)
        left = split[0].strip()
        right = split[1].strip()
        left_values = []

        # Computing left value
        if left.startswith(":"):
            left_values += [criterion._orig[0].effective_value]
        else:
            left_values += [getattr_rec(value, left.capitalize())]


        # Computing right value
        if right.startswith(":"):
            right_value = criterion._orig[1].effective_value
        else:
            if isinstance(criterion._orig[1], bool):
                right_value = criterion._orig[1]
            else:
                right_type_name = "none"
                try:
                    right_type_name = str(criterion._orig[1].type)
                except:
                    pass

                if right_type_name == "BOOLEAN":
                    right_value = right
                    if right_value == "1":
                        right_value = True
                    else:
                        right_value = False
                else:
                    right_value = getattr_rec(value, right.capitalize())

        # try:
        #     print(">>> (%s)[%s] = %s <-> %s" % (value.keys(), left, left_values, right))
        # except:
        #     pass

        result = False
        for left_value in left_values:
                
            if isinstance(left_value, datetime.datetime):
                if left_value.tzinfo is None:
                    left_value = pytz.utc.localize(left_value)

            if isinstance(right_value, datetime.datetime):
                if right_value.tzinfo is None:
                    right_value = pytz.utc.localize(right_value)

            if comparator(left_value, right_value):
                result = True

        if op == "IN":
            result = False
            right_terms = set(criterion.right.element)

            if left_value is None and hasattr(value, "__iter__"):
                left_key = left.split(".")[-1]
                if value[0].has_key(left_key):
                    left_value = value[0][left_key]

            for right_term in right_terms:
                try:
                    right_value = getattr(right_term.value, "%s" % (right_term._orig_key))
                except AttributeError:
                    right_value = right_term.value
                
                if isinstance(left_value, datetime.datetime):
                    if left_value.tzinfo is None:
                        left_value = pytz.utc.localize(left_value)

                if isinstance(right_value, datetime.datetime):
                    if right_value.tzinfo is None:
                        right_value = pytz.utc.localize(right_value)

                if comparator(left_value, right_value):
                    result = True

        return result

    def all(self):

        result_list = self.construct_rows()

        result = []
        for r in result_list:
            ok = True

            if ok:
                result += [r]
        return result

    def first(self):
        rows = self.all()
        if len(rows) > 0:
            return rows[0]
        else:
            None

    def exists(self):
        return self.first() is not None

    def count(self):
        return len(self.all())

    def soft_delete(self, synchronize_session=False):
        return self

    def update(self, values, synchronize_session='evaluate'):

        try:
            from desimplifier import ObjectDesimplifier
        except:
            pass
            
        rows = self.all()
        for row in rows:
            tablename = self.find_table_name(row)
            id = row.id

            print("[DEBUG-UPDATE] I shall update %s@%s with %s" % (str(id), tablename, values))

            object_bucket = dbClient.bucket(tablename)

            key_as_string = "%d" % (id)
            data = object_bucket.get(key_as_string).data

            for key in values:
                data[key] = values[key]

            object_desimplifier = ObjectDesimplifier()
            
            try:
                desimplified_object = object_desimplifier.desimplify(data)
                desimplified_object.save()
            except Exception as e:
                traceback.print_exc()
                print("[DEBUG-UPDATE] could not save %s@%s" % (str(id), tablename))
                return None

        return len(rows)

    ####################################################################################################################
    # Query construction
    ####################################################################################################################

    def filter_by(self, **kwargs):
        _func = self._funcs[:]
        _criterions = self._criterions[:]
        for a in kwargs:
            for selectable in self._models:
                try:
                    column = getattr(selectable._model, a)
                    _criterions += [column.__eq__(kwargs[a])]
                    break
                except Exception as e:
                    # create a binary expression
                    traceback.print_exc()
        args = self._models + _func + _criterions + self._initial_models
        return RiakModelQuery(*args)

    # criterions can be a function
    def filter(self, *criterions):
        _func = self._funcs[:]
        _criterions = self._criterions[:]
        for criterion in criterions:
            _criterions += [criterion]
        args = self._models + _func + _criterions + self._initial_models
        return RiakModelQuery(*args)

    def join(self, *args, **kwargs):
        _func = self._funcs[:]
        _models = self._models[:]
        _criterions = self._criterions[:]
        for arg in args:

            if not isinstance(arg, list) and not isinstance(arg, tuple):
               tuples = [arg]
            else:
                tuples = arg

            for item in tuples:
                is_class = inspect.isclass(item)
                is_expression = isinstance(item, BinaryExpression)
                if is_class:
                    _models = [Selection(item, "*")] + _models
                elif is_expression:
                    _criterions += [item]
                else:
                    pass
        args = _models + _func + _criterions + self._initial_models
        return RiakModelQuery(*args)

    def outerjoin(self, *args, **kwargs):
        return self.join(*args, **kwargs)

    def options(self, *args):
        _func = self._funcs[:]
        _models = self._models[:]
        _criterions = self._criterions[:]
        _initial_models = self._initial_models[:]
        args = _models + _func + _criterions + _initial_models
        return RiakModelQuery(*args)

    def order_by(self, *criterion):
        _func = self._funcs[:]
        _models = self._models[:]
        _criterions = self._criterions[:]
        _initial_models = self._initial_models[:]
        args = _models + _func + _criterions + _initial_models
        return RiakModelQuery(*args)

    def with_lockmode(self, mode):
        return self


    def subquery(self):
        _func = self._funcs[:]
        _models = self._models[:]
        _criterions = self._criterions[:]
        _initial_models = self._initial_models[:]
        args = _models + _func + _criterions + _initial_models
        return RiakModelQuery(*args).all()

    def __iter__(self):
        return iter(self.all())