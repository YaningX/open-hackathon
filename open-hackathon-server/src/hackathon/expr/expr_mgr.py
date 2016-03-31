# -*- coding: utf-8 -*-
#
# -----------------------------------------------------------------------------------
# Copyright (c) Microsoft Open Technologies (Shanghai) Co. Ltd.  All rights reserved.
#
# The MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
# -----------------------------------------------------------------------------------

import sys

sys.path.append("..")
from datetime import timedelta
import json
import random
import string
import pexpect

from werkzeug.exceptions import PreconditionFailed, NotFound
from os.path import dirname, realpath, abspath
from mongoengine import Q

from hackathon import Component, RequiredFeature, Context
from hackathon.constants import EStatus, VERemoteProvider, VE_PROVIDER, VEStatus, ReservedUser, \
    HACK_NOTICE_EVENT, HACK_NOTICE_CATEGORY
from hackathon.hmongo.models import VirtualEnvironment, DockerHostServer, Experiment, User, Template
from hackathon.hackathon_response import internal_server_error, not_found, ok

__all__ = ["ExprManager"]


class ExprManager(Component):
    register_manager = RequiredFeature("register_manager")
    user_manager = RequiredFeature("user_manager")
    hackathon_manager = RequiredFeature("hackathon_manager")
    admin_Manager = RequiredFeature("admin_manager")
    template_library = RequiredFeature("template_library")
    hackathon_template_manager = RequiredFeature("hackathon_template_manager")
    hosted_docker = RequiredFeature("hosted_docker")
    alauda_docker = RequiredFeature("alauda_docker")
    team_manager = RequiredFeature("team_manager")
    azure_formation = RequiredFeature("azure_formation")
    azure_cert_manager = RequiredFeature("azure_cert_manager")

    def start_expr(self, user, template_name, hackathon_name=None):
        """
        A user uses a template to start a experiment under a hackathon
        :param hackathon_name:
        :param template_name:
        :param user_id:
        :return:
        """

        self.log.debug("try to start experiment for hackathon %s using template %s" % (hackathon_name, template_name))
        hackathon = self.__check_hackathon_event_time(hackathon_name)
        template = self.__check_template_status(hackathon, template_name)

        if user:
            expr = self.__check_expr_status(user, hackathon, template)
            if expr:
                return self.__report_expr_status(expr)

        # new expr
        return self.__start_new_expr(hackathon, template, user_id)

    def heart_beat(self, expr_id):
        expr = self.db.find_first_object_by(Experiment, id=expr_id, status=EStatus.RUNNING)
        if expr is None:
            return not_found('Experiment is not running')

        expr.last_heart_beat_time = self.util.get_now()
        self.db.commit()
        return ok()

    def stop_expr(self, expr_id, force=0):
        """
        :param expr_id: experiment id
        :param force: 0: only stop container and release ports, 1: force stop and delete container and release ports.
        :return:
        """
        self.log.debug("begin to stop %d" % expr_id)
        expr = self.db.find_first_object_by(Experiment, id=expr_id, status=EStatus.RUNNING)
        if expr is not None:
            # Docker
            if expr.template.provider == VE_PROVIDER.DOCKER:
                # stop containers
                for c in expr.virtual_environments.all():
                    try:
                        self.log.debug("begin to stop %s" % c.name)
                        docker = self.__get_docker(expr.hackathon, c)
                        if force:
                            docker.delete(c.name, virtual_environment=c, container=c.container, expr_id=expr_id)
                            c.status = VEStatus.DELETED
                        else:
                            docker.stop(c.name, virtual_environment=c, container=c.container, expr_id=expr_id)
                            c.status = VEStatus.STOPPED
                    except Exception as e:
                        self.log.error(e)
                        self.__roll_back(expr_id)
                        return internal_server_error('Failed stop/delete container')
                if force:
                    expr.status = EStatus.DELETED
                else:
                    expr.status = EStatus.STOPPED
                self.db.commit()
            else:
                try:
                    # todo support delete azure vm
                    # hosted_docker = RequiredFeature("hosted_docker")
                    # af = AzureFormation(hosted_docker.load_azure_key_id(expr_id))
                    # af.stop(expr_id, AVMStatus.STOPPED_DEALLOCATED)
                    template = self.db.get_object(Template, expr.template_id)
                    template_content = self.template_library.load_template(template)
                    azure_keys = self.azure_cert_manager.get_certificates_by_expr(expr_id)
                    # TODO: which key to use
                    azure_key = azure_keys[0]

                    # TODO: elimate virtual_environments arg and expr_id arg
                    self.azure_formation.stop_vm(
                        expr_id, azure_key, template_content.units, expr.virtual_environments.all(), expr_id)
                except Exception as e:
                    self.log.error(e)
                    return internal_server_error('Failed stopping azure')

            self.log.debug("experiment %d ended success" % expr_id)
            return ok('OK')
        else:
            return ok()

    def get_expr_status(self, expr_id):
        expr = Experiment.objects(id==expr_id).first()
        if expr:
            return self.__report_expr_status(expr)
        else:
            return not_found('Experiment Not found')

    def check_expr_status(self, experiment):
        # update experiment status
        virtual_environment_list = experiment.virtual_environments.all()
        if all(x.status == VEStatus.RUNNING for x in virtual_environment_list) \
                and len(virtual_environment_list) == experiment.template.virtual_environment_count:
            experiment.status = EStatus.RUNNING
            self.db.commit()
            self.template_library.template_verified(experiment.template.id)

    def get_expr_list_by_hackathon_id(self, hackathon_id, **kwargs):
        # get a list of all experiments' detail
        condition = self.__get_filter_condition(hackathon_id, **kwargs)
        experiments = self.db.find_all_objects(Experiment, condition)
        return [self.__get_expr_with_detail(experiment) for experiment in experiments]

    def scheduler_recycle_expr(self):
        """recycle experiment acrroding to hackathon basic info on recycle configuration

        According to the hackathon's basic info on 'recycle_enabled', find out time out experiments
        Then call function to recycle them

        :return:
        """
        self.log.debug("start checking recyclable experiment ... ")
        for hackathon in self.hackathon_manager.get_recyclable_hackathon_list():
            # check recycle enabled
            mins = self.hackathon_manager.get_recycle_minutes(hackathon)
            expr_time_cond = Experiment.create_time < self.util.get_now() - timedelta(minutes=mins)
            status_cond = Experiment.status == EStatus.RUNNING
            # filter out the experiments that need to be recycled
            exprs = self.db.find_all_objects(Experiment,
                                             status_cond,
                                             expr_time_cond,
                                             Experiment.hackathon_id == hackathon.id)
            for expr in exprs:
                self.__recycle_expr(expr)

    def pre_allocate_expr(self, context):
        hackathon_id = context.hackathon_id
        self.log.debug("executing pre_allocate_expr for hackathon %s " % hackathon_id)
        htrs = self.db.find_all_objects_by(HackathonTemplateRel, hackathon_id=hackathon_id)
        for rel in htrs:
            try:
                template = rel.template
                pre_num = rel.hackathon.get_pre_allocate_number()
                curr_num = self.db.count(Experiment,
                                         Experiment.user_id == ReservedUser.DefaultUserID,
                                         Experiment.hackathon_id == hackathon_id,
                                         Experiment.template_id == template.id,
                                         (Experiment.status == EStatus.STARTING) | (
                                             Experiment.status == EStatus.RUNNING))
                if template.provider == VE_PROVIDER.AZURE:
                    if curr_num < pre_num:
                        remain_num = pre_num - curr_num
                        start_num = self.db.count_by(Experiment,
                                                     user_id=ReservedUser.DefaultUserID,
                                                     template=template,
                                                     status=EStatus.STARTING)
                        if start_num > 0:
                            self.log.debug("there is an azure env starting, will check later ... ")
                            return
                        else:
                            self.log.debug(
                                "no starting template: %s , remain num is %d ... " % (template.name, remain_num))
                            self.start_expr(None, template.name, rel.hackathon.name)
                            break
                            # curr_num += 1
                            # self.log.debug("all template %s start complete" % template.name)
                elif template.provider == VE_PROVIDER.DOCKER:
                    if rel.hackathon.is_alauda_enabled():
                        # don't create pre-env if alauda used
                        continue

                    self.log.debug(
                        "template name is %s, hackathon name is %s" % (template.name, rel.hackathon.name))
                    if curr_num < pre_num:
                        remain_num = pre_num - curr_num
                        self.log.debug("no idle template: %s, remain num is %d ... " % (template.name, remain_num))
                        self.start_expr(None, template.name, rel.hackathon.name)
                        # curr_num += 1
                        break
                        # self.log.debug("all template %s start complete" % template.name)
            except Exception as e:
                self.log.error(e)
                self.log.error("check default experiment failed")

    def assign_expr_to_admin(self, expr):
        """assign expr to admin to trun expr into pre_allocate_expr

        :type expr: Experiment
        :param expr: which expr you want to assign

        :return:
        """
        try:
            self.db.update_object(expr, user_id=ReservedUser.DefaultUserID)
        except Exception as e:
            self.log.error(e)

    # --------------------------------------------- helper function ---------------------------------------------#

    def __check_hackathon_event_time(self, hackathon_name):
        """validate the event_start_time and event_end_time of a hackathon

        Will return None if hackathon not found or current time is not between its start time and end time
        """
        hackathon = self.hackathon_manager.get_hackathon_by_name(hackathon_name)
        if hackathon:
            if self.util.get_now() < hackathon.event_end_time:
                return hackathon
            else:
                raise PreconditionFailed("Hackathon was already ended")
        else:
            raise NotFound("Hackathon with name %s not found" % hackathon_name)

    def __get_docker(self, hackathon, virtual_environment=None):
        """select which docker implementation"""
        if virtual_environment:
            if virtual_environment.provider == VE_PROVIDER.ALAUDA:
                return self.alauda_docker
            return self.hosted_docker
        elif hackathon.is_alauda_enabled():
            return self.alauda_docker
        else:
            return self.hosted_docker

    def __start_new_expr(self, hackathon, template, user_id):
        # new expr
        expr = Experiment(user)
        expr = self.db.add_object_kwargs(Experiment,
                                         user_id=user_id,
                                         hackathon_id=hackathon.id,
                                         status=EStatus.INIT,
                                         template_id=template.id,
                                         create_time=self.util.get_now())
        self.db.commit()

        if template.provider == VE_PROVIDER.DOCKER:
            try:
                template_content = self.template_library.load_template(template)
                virtual_environments_units = template_content.units

                expr.status = EStatus.STARTING
                self.db.commit()
                map(lambda unit:
                    self.__remote_start_container(hackathon, expr, unit, user_id),
                    virtual_environments_units)
            except Exception as e:
                self.log.error(e)
                self.log.error("Failed starting containers")
                self.roll_back(expr.id)
                return internal_server_error('Failed starting containers')
        else:
            expr.status = EStatus.STARTING
            self.db.commit()
            try:
                # af = AzureFormation(self.hosted_docker.load_azure_key_id(expr.id))
                # af.create(expr.id)
                template_content = self.template_library.load_template(template)
                azure_keys = self.azure_cert_manager.get_certificates_by_expr(expr.id)
                # TODO: which key to use?
                azure_key = azure_keys[0]

                # create virtual environments for units
                expr_id = expr.id
                ves = []
                for unit in template_content.units:
                    ve = VirtualEnvironment(
                        provider=VE_PROVIDER.AZURE,
                        # TODO: when to set name?
                        name=self.azure_formation.get_virtual_machine_name(unit.get_virtual_machine_name(), expr_id),
                        image=unit.get_image_name(),
                        status=VEStatus.INIT,
                        remote_provider=VERemoteProvider.Guacamole,
                        experiment=expr)
                    self.db.add_object(ve)
                    ves.append(ve)

                # TODO: elimate virtual_environments arg
                self.azure_formation.start_vm(expr_id, azure_key, template_content.units, ves)
            except Exception as e:
                self.log.error(e)
                return internal_server_error('Failed starting azure vm')
        # after everything is ready, set the expr state to running
        # response to caller

        self.hackathon_manager.create_hackathon_notice(hackathon.id, HACK_NOTICE_EVENT.EXPR_JOIN,
                                                       HACK_NOTICE_CATEGORY.EXPERIMENT, {'user_id': user_id})
        return self.__report_expr_status(expr)

    def __report_expr_status(self, expr):
        expr = self.__re_check_expr_status(expr)

        ret = {
            "expr_id": str(expr.id),
            "status": expr.status,
            "hackathon_name": expr.hackathon.name if expr.hackathon else "",
            "hackathon": str(expr.hackathon.id) if expr.hackathon else "",
            "create_time": str(expr.create_time),
            "last_heart_beat_time": str(expr.last_heart_beat_time),
        }

        if expr.status != EStatus.RUNNING:
            return ret

        # return remote clients include guacamole
        remote_servers = []
        for ve in expr.virtual_environments:
            if ve.remote_provider == VERemoteProvider.Guacamole:
                try:
                    guacamole_config = ve.remote_paras
                    guacamole_host = self.util.safe_get_config("guacamole.host", "localhost:8080")
                    # target url format:
                    # http://localhost:8080/guacamole/#/client/c/{name}?name={name}&oh={token}
                    name = guacamole_config["name"]
                    url = guacamole_host + '/guacamole/#/client/c/%s?name=%s' % (name, name)
                    remote_servers.append({
                        "name": guacamole_config["name"],
                        "guacamole_host": guacamole_host,
                        "url": url
                    })

                except Exception as e:
                    self.log.error(e)
                    # so that the frontend can query again?
                    ret["status"] = EStatus.STARTING
                    return ret

        ret["remote_servers"] = remote_servers

        # return public accessible web url
        public_urls = []
        if expr.template.provider == VE_PROVIDER.DOCKER:
            for ve in expr.virtual_environments:
                container = ve.docker_container
                for p in container.port_bindings.filter(is_public=True):
                    if p.url:
                        public_urls.append({
                            "name": p.name,
                            "url": p.url.format(container.host_server.public_dns, p.public_port)
                        })
        else:
            # todo windows azure public url
            for ve in expr.virtual_environments:
                for vm in ve.azure_virtual_machines_v.all():
                    ep = vm.azure_endpoints.filter_by(private_port=80).first()
                    url = 'http://%s:%s' % (vm.public_ip, ep.public_port)
                    public_urls.append({
                        "name": ep.name,
                        "url": url
                    })
        ret["public_urls"] = public_urls
        return ret

    def __check_template_status(self, hackathon, template_name):
        template = self.template_library.get_template_info_by_name(template_name)
        if not template:
            raise NotFound("template cannot be found by name '%s'" % template_name)

        if not hackathon:
            # hackathon is None means it's starting expr for template testing
            return template

        hackathon_templates = hackathon.templates
        template_ids = [t.id for t in hackathon_templates]
        if template.id not in template_ids:
            raise PreconditionFailed("template '%s' not allowed for hackathon '%s'" % (template_name, hackathon.name))

        return template

    def __remote_start_container(self, hackathon, expr, docker_template_unit, user_id):
        old_name = docker_template_unit.get_name()
        suffix = "".join(random.sample(string.ascii_letters + string.digits, 8))
        new_name = '%d-%s-%s' % (expr.id, old_name, suffix.lower())
        docker_template_unit.set_name(new_name)
        self.log.debug("starting to start container: %s" % new_name)
        # db entity
        ve = VirtualEnvironment(provider=VE_PROVIDER.DOCKER,
                                name=new_name,
                                image=docker_template_unit.get_image_with_tag(),
                                status=VEStatus.INIT,
                                remote_provider=VERemoteProvider.Guacamole,
                                experiment=expr)
        self.db.add_object(ve)
        self.db.commit()
        # start container remotely , use hosted docker or alauda docker
        docker = self.__get_docker(hackathon)
        docker.start(docker_template_unit,
                     hackathon=hackathon,
                     experiment=expr,
                     user_id=user_id,
                     new_name=new_name)

        self.log.debug("starting container %s is ended ... " % new_name)
        return ve

    def on_docker_completed(self, ve):
        """
        This function should be invoked after container is started in alauda_docker.py and hosted_docker.py
        :param ve: virtual environment
        """
        remote = json.loads(ve.remote_paras)
        try:
            p = pexpect.spawn("scp -P %s %s %s@%s:/usr/local/sbin/guacctl" % (remote["port"],
                                                                              abspath("%s/../docker/guacctl" % dirname(
                                                                                  realpath(__file__))),
                                                                              remote["username"],
                                                                              remote["hostname"]))
            i = p.expect([pexpect.TIMEOUT, 'yes/no', 'password: '])

            if i == 1:
                p.sendline("yes")
                i = p.expect([pexpect.TIMEOUT, 'password:'])

            if i != 0:
                p.sendline(remote["password"])
                p.expect(pexpect.EOF)

            p.close()
        except Exception as e:
            self.log.info("scp file error")
            self.log.error(e)
        return

    def __check_expr_status(self, user, hackathon, template):
        """
        check experiment status, if there are pre-allocate experiments, the experiment will be assigned directly
        :param user:
        :param hackathon:
        :param template:
        :return:
        """
        criterion = Q(status__in=[EStatus.RUNNING, EStatus.STARTING], hackathon=hackathon)
        is_admin = self.admin_Manager.is_hackathon_admin(hackathon.id, user.id)
        if is_admin:
            criterion &= Q(template=template)

        expr = Experiment.objects(criterion).first()
        if expr:
            # user has a running/starting experiment
            return expr

        # try to assign pre-configured expr to user
        expr = Experiment.objects(status=EStatus.RUNNING, hackathon=hackathon, template=template, user=None).first()
        if expr:
            expr.user = user
            expr.save()
            return expr

    def roll_back(self, expr_id):
        """
        roll back when exception occurred
        :param expr_id: experiment id
        """
        self.log.debug("Starting rollback experiment %d..." % expr_id)
        expr = self.db.find_first_object_by(Experiment, id=expr_id)
        try:
            expr.status = EStatus.ROLL_BACKING
            self.db.commit()
            if expr is not None:
                # delete containers and change expr status
                for c in expr.virtual_environments:
                    if c.provider == VE_PROVIDER.DOCKER and c.container:
                        docker = self.__get_docker(expr.hackathon, c)
                        docker.delete(c.name, container=c.container, expr_id=expr_id)
                        c.status = VEStatus.DELETED
                        self.db.commit()
            # delete ports
            expr.status = EStatus.ROLL_BACKED

            self.db.commit()
            self.log.info("Rollback succeeded")
        except Exception as e:
            expr.status = EStatus.FAILED
            self.db.commit()
            self.log.info("Rollback failed")
            self.log.error(e)

    def __get_expr_with_detail(self, experiment):
        info = experiment.dic()
        info['user_info'] = self.user_manager.user_display_info(experiment.user)
        virtual_environments = self.db.find_all_objects_by(VirtualEnvironment, experiment_id=experiment.id)

        def get_virtual_environment_detail(virtual_environment):
            dict = virtual_environment.dic()
            containers_detail = self.hosted_docker.get_containers_detail_by_ve(virtual_environment)
            if not containers_detail == {}:
                dict["hosted_docker"] = containers_detail
            return dict

        info['virtual_environments'] = [get_virtual_environment_detail(ve) for ve in virtual_environments]
        return info

    def __get_filter_condition(self, hackathon_id, **kwargs):
        condition = Experiment.hackathon_id == hackathon_id
        # check status: -1 means query all status
        if kwargs['status'] != -1:
            condition = and_(condition, Experiment.status == kwargs['status'])
        # check user name
        if len(kwargs['user_name']) > 0:
            users = self.db.find_all_objects(User, User.nickname.like('%' + kwargs['user_name'] + '%'))
            uids = map(lambda x: x.id, users)
            condition = and_(condition, Experiment.user_id.in_(uids))
        return condition

    def __re_check_expr_status(self, expr):
        """get experiment's all containers which are based on hosted_docker

        :type  expr: Experiment
        :param expr: which to get containers from

        :return DockerContainer object List by query

        """
        if expr.status != EStatus.STARTING:
            for ve in expr.virtual_environments:
                if ve.provider != VE_PROVIDER.DOCKER or ve.docker_container is None:
                    continue
                # expr status(restarting or running) is not match container running status on docker host
                try:
                    if not self.hosted_docker.check_container_status_is_normal(ve.docker_container):
                        expr.status = EStatus.UNEXPECTED_ERROR
                        ve.status = VEStatus.UNEXPECTED_ERROR
                        expr.save()
                        break
                except Exception as ex:
                    self.log.error(ex)
        return expr

    def __recycle_expr(self, expr):
        """recycle expr

        If it is a docker experiment , stop it ; else assign it to default user

        :type expr: Experiment
        :param expr: the exper which you want to recycle

        :return:
        """
        providers = map(lambda x: x.provider, expr.virtual_environments.all())
        if VE_PROVIDER.DOCKER in providers:
            self.stop_expr(expr.id)
            self.log.debug("it's stopping " + str(expr.id) + " inactive experiment now")
        else:
            self.assign_expr_to_admin(expr)
            self.log.debug("assign " + str(expr.id) + " to default admin")
