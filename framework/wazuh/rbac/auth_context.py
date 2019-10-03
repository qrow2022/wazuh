# Copyright (C) 2015-2019, Wazuh Inc.
# Created by Wazuh, Inc. <info@wazuh.com>.
# This program is a free software; you can redistribute it and/or modify it under the terms of GPLv2

import json
import re

from wazuh.rbac import orm
from wazuh.exception import WazuhInternalError


class RBAChecker:
    """
    The logical operations available in our system:
        AND: All the clauses that it encloses must be certain so that the operation is evaluated as certain.
        OR: At least one of the clauses it contains must be correct for the operator to be evaluated as True.
        NOT: The clause enclosed by the NOT operator must give False for it to be evaluated as True.
        All these operations can be nested
    These are the functions available in our role based login system:
        MATCH: This operation checks that the clause or clauses that it encloses are in the authorization context
          that comes to us. If there is no occurrence, return False. If any clause in the context authorization
          encloses our MATCH, it will return True because it is encapsulated in a larger set.
        MATCH$: It works like the previous operation with the difference that it is more strict. In this case the
          occurrence must be exact, it will be evaluated as False although the clause is included in a larger one
          in the authorization context.
        FIND: Recursively launches the MATCH function to search for all levels of authorization context, the
          operation is the same, if there is at least one occurrence, the function will return True
        FIND$: Just like the previous one, in this case the function MATCH$ is recursively executed.
    Regex schema ----> "r'REGULAR_EXPRESSION', this is the wildcard for detecting regular expressions"
    """
    _logical_operators = ['AND', 'OR', 'NOT']
    _functions = ['MATCH', 'MATCH$', 'FIND', 'FIND$']
    _initial_index_for_regex = 2
    _regex_prefix = "r'"

    # If we don't pass it the role to check, it will take all of the system.
    def __init__(self, auth_context, role=None):
        """Class constructor to match the roles of the system with a given authorization context

        :param auth_context: Authorization context to be checked
        :param role: Roles(list)/Role/None(All roles in the system) to be checked against the authorization context
        """
        self.authorization_context = json.loads(auth_context)
        # All roles in the system
        if role is None:
            with orm.RolesManager() as rm:
                self.roles_list = rm.get_roles()
                for role in self.roles_list:
                    role.rule = json.loads(role.rule)
        else:
            # One single role
            if not isinstance(role, list):
                self.roles_list = [role]
                self.roles_list[0].rule = json.loads(role.rule)
            # role is a list of roles
            elif isinstance(role, list):
                self.roles_list = role
                for role in self.roles_list:
                    role.rule = json.loads(role.rule)

    def get_authorization_context(self):
        """Return the authorization context

        :return: Provided authorization context
        """
        return self.authorization_context

    def get_roles(self):
        """Return all roles

        :return: List of roles to handle
        """
        return self.roles_list

    @staticmethod
    def preprocess_to_list(role_chunk, auth_chunk):
        """Assigns the correct type to authorization context and role chunks

        :param role_chunk: Role chunk
        :param auth_chunk: Authorization context chunk
        :return: List with role_chunk and auth_chunk processed
        """
        role_chunk = sorted(role_chunk) if isinstance(role_chunk, list) else role_chunk
        auth_chunk = sorted(auth_chunk) if isinstance(auth_chunk, list) else auth_chunk

        return role_chunk, auth_chunk

    def process_lists(self, role_chunk: list, auth_context: list, mode):
        """Process lists of role chunks and authorization context chunks

        :param role_chunk: List inside the role
        :param auth_context: List inside the auth_context
        :param mode: Mode to match both lists
        :return: 1 or 0, 1 if the function is evaluated as True else return False
        """
        counter = 0
        for index, value in enumerate(auth_context):
            for v in role_chunk:
                regex = self.check_regex(v)
                if regex:
                    if regex.match(value):
                        counter += 1
                else:
                    if value == v:
                        counter += 1
                if mode == self._functions[0]:  # MATCH
                    if counter == len(role_chunk):
                        return 1
                elif mode == self._functions[1]:  # MATCH$
                    if counter == len(auth_context) and counter == len(role_chunk):
                        return 1

        return 0

    def set_mode(self, mode, role_id=None):
        """Links the FIND/FIND$ modes with their respective functions (MATCH/MATCH$)

        :param mode: FIND/FIND$
        :param role_id: Actual role id to be checked
        :return mode: FIND -> MATCH | FIND$ -> MATCH$
        """
        if mode == self._functions[2]:  # FIND
            mode = self._functions[0]  # MATCH
        elif mode == self._functions[3]:  # FIND$
            mode = self._functions[1]  # MATCH$

        return mode

    def check_logic_operation(self, rule_key, rule_value, validator_counter):
        """Evaluate a specified logic operation role-auth_context

        :param rule_key: Possible logic operation
        :param rule_value: Clause to be evaluated
        :param validator_counter: Number of successes within the logical operation
        :return: True/False/None, it is possible that the database has been modified externally to Wazuh,
        Potential Security Breach, Currently, if this is the case and the unknown role is invalid, it will not
        cause any problems to the system, it will be ignored.
        """
        if rule_key == self._logical_operators[0]:  # AND
            if validator_counter == len(rule_value):
                return True
        elif rule_key == self._logical_operators[1]:  # OR
            if validator_counter > 0:
                return True
        elif rule_key == self._logical_operators[2]:  # NOT
            return False if validator_counter == len(rule_value) else True

        return None

    def check_regex(self, expression):
        """Checks if a certain string is a regular expression

        :param expression: Regular expression to be checked
        :return: Compiled regex if a valid regex is provided else return False
        """
        if isinstance(expression, str):
            if not expression.startswith(self._regex_prefix):
                return False
            try:
                regex = ''.join(expression[self._initial_index_for_regex:-2])
                regex = re.compile(regex)
                return regex
            except:
                return False
        return False

    def match_item(self, role_chunk, auth_context=None, mode='MATCH'):
        """This function will go through all authorization contexts and system roles
        recursively until it finds the structure indicated in role_chunk

        :param role_chunk: Chunk of one stored role in the class
        :param auth_context: Received authorization context
        :param mode: MATCH or MATCH$
        :return: True if match else False
        """
        auth_context = self.authorization_context if auth_context is None else auth_context
        validator_counter = 0
        # We're not in the deep end yet.
        if isinstance(role_chunk, dict) and isinstance(auth_context, dict):
            for key_rule, value_rule in role_chunk.items():
                regex = self.check_regex(key_rule)
                if regex:
                    for key_auth in auth_context.keys():
                        if regex.match(key_auth):
                            validator_counter += self.match_item(role_chunk[key_rule], auth_context[key_auth], mode)
                if key_rule in auth_context.keys():
                    validator_counter += self.match_item(role_chunk[key_rule], auth_context[key_rule], mode)
        # It's a possible end
        else:
            role_chunk, auth_context = self.preprocess_to_list(role_chunk, auth_context)
            regex = self.check_regex(role_chunk)
            if regex:
                if not isinstance(auth_context, list):
                    auth_context = [auth_context]
                for context in auth_context:
                    if regex.match(context):
                        return 1
            if role_chunk == auth_context:
                return 1
            if isinstance(role_chunk, str):
                role_chunk = [role_chunk]
            if isinstance(role_chunk, list) and isinstance(auth_context, list):
                return self.process_lists(role_chunk, auth_context, mode)
        if isinstance(role_chunk, dict):
            if validator_counter == len(role_chunk.keys()):
                return True

        return False

    def find_item(self, role_chunk, auth_context=None, mode='FIND', role_id=None):
        """This function will use the match function and will launch it recursively on
        all the authorization context tree, on all the levels.

        :param role_chunk: Chunk of one stored role in the class
        :param auth_context: Received authorization context
        :param mode: FIND -> MATCH | FIND$ -> MATCH$
        :param role_id: ID of the current role
        :return:
        """
        auth_context = self.authorization_context if auth_context is None else auth_context
        mode = self.set_mode(mode, role_id)

        validator_counter = self.match_item(role_chunk, auth_context, mode)
        if validator_counter:
            return True

        for key, value in auth_context.items():
            if self.match_item(role_chunk, value, mode):
                return True
            elif isinstance(value, dict):
                if self.find_item(role_chunk, value, mode=mode):
                    return True
            elif isinstance(value, list):
                for v in value:
                    if isinstance(v, dict):
                        if self.find_item(role_chunk, v, mode=mode):
                            return True

        return False

    def check_rule(self, rule, role_id=None):
        """This is the controller for the match of the roles with the authorization context,
        this function is the one that will launch the others.

        :param rule: The rule of the current role
        :param role_id: ID of the current role
        :return:
        """
        for rule_key, rule_value in rule.items():
            if rule_key in self._logical_operators:  # The current key is a logical operator
                validator_counter = 0
                if isinstance(rule_value, list):
                    for element in rule_value:
                        validator_counter += self.check_rule(element)
                elif isinstance(rule_value, dict):
                    validator_counter += self.check_rule(rule_value)
                result = self.check_logic_operation(rule_key, rule_value, validator_counter)
                if isinstance(result, bool):
                    return result
            elif rule_key in self._functions:  # The current key is a function
                if rule_key == self._functions[0] or rule_key == self._functions[1]:  # MATCH, MATCH$
                    if self.match_item(role_chunk=rule[rule_key], mode=rule_key):
                        return 1
                elif rule_key == self._functions[2] or rule_key == self._functions[3]:  # FIND, FIND$
                    if self.find_item(role_chunk=rule[rule_key], mode=rule_key, role_id=role_id):
                        return 1

        return False

    # A list will be filled with the names of the roles that the user has.
    def get_user_roles(self):
        list_roles = list()
        for role in self.roles_list:
            list_roles.append([role.id, role.name]) if self.check_rule(role.rule) else None

        return list_roles

    def run(self):
        user_roles = self.get_user_roles()
        user_policies = []
        with orm.RolesPoliciesManager() as rpm:
            for role in user_roles:
                user_policies.append(policy for policy in rpm.get_all_policies_from_role(role[0]))
            user_policies = set(user_policies)

        return user_policies

    # This is for TESTING. This method returns a list of hardcoded policies for testing
    @staticmethod
    def run_testing():
        policies = [
            {
                "actions": ["syscheck:put", "syscheck:get", "syscheck:delete"],
                "resources": ["agent:id:*"],
                "effect": "allow"
            },
            {
                "actions": ["lists:get"],
                "resources": ["list:path:*"],
                "effect": "allow"
            },
            {
                "actions": ["active_response:command"],
                "resources": ["agent:id:001"],
                "effect": "allow"
            },
            {
                "actions": ["active_response:command"],
                "resources": ["agent:id:001", "agent:id:002"],
                "effect": "deny"
            },
            {
                "actions": ["active_response:command"],
                "resources": ["agent:id:001", "agent:id:002", "agent:id:004"],
                "effect": "deny"
            },
            {
                "actions": ["active_response:command"],
                "resources": ["agent:id:001", "agent:id:002"],
                "effect": "deny"
            },
            {
                "actions": ["active_response:command"],
                "resources": ["agent:group:default"],
                "effect": "allow"
            },
            {
                "actions": ["active_response:command"],
                "resources": ["agent:group:group1"],
                "effect": "deny"
            },
            {
                "actions": ["agent:delete"],
                "resources": ["agent:id:*"],
                "effect": "allow"
            },
            {
                "actions": ["agent:delete"],
                "resources": ["agent:id:099"],
                "effect": "allow"
            },
            {
                "actions": ["agent:delete"],
                "resources": ["agent:id:003"],
                "effect": "deny"
            },
            {
                "actions": ["agent:delete"],
                "resources": ["agent:group:group1"],
                "effect": "deny"
            },
            {
                "actions": ["agent:delete"],
                "resources": ["agent:group:group2"],
                "effect": "allow"
            },
            {
                "actions": ["agent:delete"],
                "resources": ["agent:id:004"],
                "effect": "allow"
            },
            {
                "actions": ["agent:read"],
                "resources": ["agent:id:*"],
                "effect": "allow"
            },
            {
                "actions": ["agent:read"],
                "resources": ["agent:id:003"],
                "effect": "deny"
            },
            {
                "actions": ["agent:read"],
                "resources": ["agent:id:099"],
                "effect": "allow"
            },
            {
                "actions": ["agent:read"],
                "resources": ["agent:group:group2"],
                "effect": "deny"
            },
            {
                "actions": ["agent:read"],
                "resources": ["agent:group:group1"],
                "effect": "allow"
            }
        ]

        return policies