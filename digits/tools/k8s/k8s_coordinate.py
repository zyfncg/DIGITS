# coding=utf-8
import os
import subprocess
import digits
import yaml
import time
from kubernetes import client, config


class KubernetasCoord(object):

    def __init__(self, logger):
        self.logger = logger
        config.load_kube_config()
        self.v1 = client.CoreV1Api()

    @staticmethod
    def get_pod_name(job_id):
        return "digits-%s" % job_id

    def get_pods_status(self, pod_label):
        """
        根据job_id获取pod状态信息
        :param job_id:
        :return:
        """

        resp = self.v1.list_namespaced_pod(namespace='default', label_selector='app=%s' % pod_label)
        status_list = []
        for i in resp.items:
            status_list.append(i.status.phase)
        return status_list

    @staticmethod
    def generate_yaml(job_dir, pod_label, node_count, gpu_count):
        """
        根据用户参数生成用于创建容器组的yaml配置文件
        :param job_dir: job路径
        :param pod_label:
        :param node_count: 节点数量
        :param gpu_count: 每个节点GPU数量
        :return: 生成的yaml文件的路径
        """

        digits_path = os.path.dirname(digits.__file__)
        yaml_path = os.path.join(digits_path, 'tools/k8s/mpi_node_base.yaml')
        new_yaml_path = os.path.join(job_dir, 'mpi-nodes.yaml')
        name = pod_label

        # 读取mpi_node_base.yaml中的内容，并将可修改的参数进行替换，保存成新的yaml文件
        with open(yaml_path, mode='r+') as r_file:
            with open(new_yaml_path, 'w+') as w_file:
                for line in r_file.readlines():
                    if line.find("$name$") >= 0:
                        new_line = line.replace("$name$", name)
                        w_file.write(new_line)
                    elif line.find("$label$") >= 0:
                        new_line = line.replace("$label$", name)
                        w_file.write(new_line)
                    elif line.find("$node_count$") >= 0:
                        new_line = line.replace("$node_count$", '%d' % node_count)
                        w_file.write(new_line)
                    elif line.find("$gpu_count$") >= 0:
                        new_line = line.replace("$gpu_count$", '%d' % gpu_count)
                        w_file.write(new_line)
                    else:
                        w_file.write(line)
        return new_yaml_path

    def create_deployment(self, yaml_path, pod_label):
        """
        创建deployment类型的容器组
        :param yaml_path: yaml文件路径
        :param pod_label: 容器组label
        :return: 无
        """

        # 读取yaml文件
        with open(yaml_path) as f:
            dep = yaml.load(f)
            resp = client.ExtensionsV1beta1Api().create_namespaced_deployment(body=dep, namespace="default")
            self.logger.info("Deployment created. status='%s'" % str(resp.status))
        running = False

        # 由于容器组启动需要一定时间，使用轮询判断deployment是否成功启动
        # 当所有容器都启动成功时，running设置为True，退出循环，结束轮询
        while not running:
            # 轮询时间间隔为1秒
            time.sleep(1)
            # 获取deployment创建状态
            status = self.get_pods_status(pod_label=pod_label)
            if len(status) > 0:
                self.logger.info(status)
                running = True
                for stat in status:
                    # 如果有任何一个容器状态不为Running,表示未全部成功启动
                    if not stat == 'Running':
                        # 未全部成功启动，需要继续轮询
                        running = False
                        break
        self.logger.info("Deployment created successfully!")

    @staticmethod
    def delete_deployment(name):
        """
        删除deployment
        :param name: 要删除的deployment的name
        :return: 无
        """
        # Delete deployment
        api_response = client.ExtensionsV1beta1Api().delete_namespaced_deployment(
            name=name,
            namespace="default",
            body=client.V1DeleteOptions(
                propagation_policy='Foreground',
                grace_period_seconds=5))
        print("Deployment deleted. status='%s'" % str(api_response.status))

    def generate_hostfile(self, pod_label, slots, job_dir):
        resp = self.v1.list_namespaced_pod(namespace='default', label_selector='app=%s' % pod_label)
        lines = []
        for i in resp.items:
            lines.append('%s slots=%d'% (i.status.pod_ip, slots))
        hostfile_path = os.path.join(job_dir, 'hostfile')
        f = open(hostfile_path, 'w')
        lines = [line + '\n' for line in lines]
        f.writelines(lines)
        f.close()

    def container_prepare(self, job_id, job_dir='/home/nfsdir/nfsdir/zyf', node_count=1, gpu_count=1, slots=1):
        pod_label = self.get_pod_name(job_id)
        yaml_path = self.generate_yaml(job_dir=job_dir, pod_label=pod_label, node_count=node_count, gpu_count=gpu_count)
        self.create_deployment(yaml_path, pod_label=pod_label)
        self.generate_hostfile(pod_label=pod_label, slots=slots, job_dir=job_dir)

    @staticmethod
    def delete_pods(job_id):
        KubernetasCoord.delete_deployment(name=KubernetasCoord.get_pod_name(job_id))

