import logging
import os
import json
import filecmp

from avocado.utils import process
from avocado.core import exceptions

from virttest import utils_misc
from virttest.libvirt_xml.backup_xml import BackupXML
from virttest.libvirt_xml.checkpoint_xml import CheckpointXML


class BackupError(Exception):

    """
    Class of backup exception
    """

    def __init__(self, *args):
        Exception.__init__(self, *args)


class BackupBeginError(BackupError):

    """
    Class of backup begin exception
    """

    def __init__(self, error_info):
        BackupError.__init__(self, error_info)
        self.error_info = error_info

    def __str__(self):
        return "Backup failed: %s" % self.error_info


def create_checkpoint_xml(cp_params, disk_param_list=None):
    """
    Create a checkpoint xml

    :param cp_params: Params of checkpoint
    :param disk_param_list: A list of disk params used in checkpoint xml
    :return: The checkponit xml
    """
    cp_xml = CheckpointXML()
    cp_name = cp_params.get("checkpoint_name")
    cp_desc = cp_params.get("checkpoint_desc")
    if not cp_name:
        raise exceptions.TestError("Checkpoint name must be provided")
    cp_xml.name = cp_name
    if cp_desc:
        cp_xml.description = cp_desc
    if disk_param_list:
        if not isinstance(disk_param_list, list):
            raise exceptions.TestError("checkpoint disk tags should be defined "
                                       "by a list.")
        cp_xml.disks = disk_param_list
    cp_xml.xmltreefile.write()

    utils_misc.wait_for(lambda: os.path.exists(cp_xml.xml), 5)
    return cp_xml


def create_backup_xml(backup_params, disk_xml_list=None):
    """
    Create a backup xml

    :param backup_params: Params of backup
    :param disk_xml_list: A list of disk params used in backup xml
    :return: The backup xml
    """
    backup_xml = BackupXML()
    backup_mode = backup_params.get("backup_mode")
    backup_incremental = backup_params.get("backup_incremental")
    backup_server = backup_params.get("backup_server")
    if backup_mode:
        backup_xml.mode = backup_mode
    if backup_incremental:
        backup_xml.incremental = backup_incremental
    if backup_server:
        if not isinstance(backup_server, dict):
            raise exceptions.TestError("Backup server tag should be defined by a dict.")
        backup_xml.server = backup_server
    backup_xml.set_disks(disk_xml_list)
    backup_xml.xmltreefile.write()

    utils_misc.wait_for(lambda: os.path.exists(backup_xml.xml), 5)
    return backup_xml


def create_backup_disk_xml(backup_disk_params):
    """
    Create a disk xml which is a subelement of a backup xml

    :param backup_disk_params: Params of disk xml
    :return: The disk xml
    """
    backup_disk_xml = BackupXML.DiskXML()
    disk_name = backup_disk_params.get("disk_name")
    disk_type = backup_disk_params.get("disk_type")
    enable_backup = backup_disk_params.get("enable_backup")
    exportname = backup_disk_params.get("exportname")
    exportbitmap = backup_disk_params.get("exportbitmap")
    backup_target = backup_disk_params.get("backup_target")  # dict
    backup_driver = backup_disk_params.get("backup_driver")  # dict
    backup_scratch = backup_disk_params.get("backup_scratch")  # dict
    if not disk_name:
        raise exceptions.TestError("Disk name must be provided for backup disk xml.")
    backup_disk_xml.name = disk_name
    if disk_type:
        backup_disk_xml.type = disk_type
    if enable_backup:
        backup_disk_xml.backup = enable_backup
    if exportname:
        backup_disk_xml.exportname = exportname
    if exportbitmap:
        backup_disk_xml.exportbitmap = exportbitmap
    if backup_target:
        if not isinstance(backup_target, dict):
            raise exceptions.TestError("disk target tag should be defined by a dict.")
        backup_disk_xml.target = backup_target
    if backup_driver:
        if not isinstance(backup_driver, dict):
            raise exceptions.TestError("disk driver tag should be defined by a dict.")
        backup_disk_xml.driver = backup_driver
    if backup_scratch:
        if not isinstance(backup_scratch, dict):
            raise exceptions.TestError("disk scratch tag should be defined by a dict.")
        backup_disk_xml.scratch = backup_scratch
    backup_disk_xml.xmltreefile.write()

    utils_misc.wait_for(lambda: os.path.exists(backup_disk_xml.xml), 5)
    return backup_disk_xml


def pull_incremental_backup_to_file(nbd_params, target_path,
                                    bitmap_name, file_size):
    """
    Dump pull-mode incremental backup data to a file

    :param nbd_params: The params of nbd service which provide the backup data
    :param target_path: The path of the file to dump the data
    :param bitmap_name: The dirty bitmap used for the backup
    :param file_size: The size of the target file to be preapred
    """
    nbd_protocol = nbd_params.get("nbd_protocol", "tcp")
    cmd = "qemu-img create -f qcow2 %s %s" % (target_path, file_size)
    logging.debug(process.run(cmd, shell=True).stdout_text)
    map_from = "--image-opts driver=nbd,export=%s,server.type=%s"
    map_from += ",%s"
    map_from += ",x-dirty-bitmap=qemu:dirty-bitmap:%s"
    nbd_export = nbd_params.get("nbd_export", "vdb")
    if nbd_protocol == "tcp":
        nbd_host = nbd_params.get("nbd_host", "127.0.0.1")
        nbd_tcp_port = nbd_params.get("nbd_tcp_port", "10809")
        nbd_type = "inet"
        nbd_server = "server.host=%s,server.port=%s" % (nbd_host, nbd_tcp_port)
        nbd_image_path = "nbd://%s:%s/%s" % (nbd_host, nbd_tcp_port, nbd_export)
    elif nbd_protocol == "unix":
        nbd_type = "unix"
        nbd_socket = nbd_params.get("nbd_socket", "/tmp/pull_backup.socket")
        nbd_server = "server.path=%s" % nbd_socket
        nbd_image_path = "nbd+unix:///%s?socket=%s" % (nbd_export, nbd_socket)
    map_from %= (nbd_export, nbd_type, nbd_server, bitmap_name)
    # Rebase backup file to nbd image
    rebase_cmd = "qemu-img rebase -u -f qcow2 -F raw -b %s %s"
    rebase_cmd %= (nbd_image_path, target_path)
    process.run(rebase_cmd, shell=True)
    # Remove backing chain of the backup file
    qemu_map_cmd = "qemu-img map --output=json %s -U" % map_from
    result = process.run(qemu_map_cmd, shell=True).stdout_text
    json_data = json.loads(result)
    for entry in json_data:
        if not entry["data"]:
            qemu_io_cmd = "qemu-io -C -c \"r %s %s\" -f qcow2 %s"
            qemu_io_cmd %= (entry["start"], entry["length"], target_path)
            process.run(qemu_io_cmd, shell=True)
    # remove the backing file info for the target image
    rebase_cmd = "qemu-img rebase -u -f qcow2 -b '' %s" % target_path
    process.run(rebase_cmd, shell=True)


def pull_full_backup_to_file(nbd_export, target_path):
    """
    Dump pull-mode full backup data to a file

    :param nbd_export: The nbd export info of the backup job
    :param target_path: The path of the file to dump the data
    """
    cmd = "qemu-img convert -f raw %s -O qcow2 %s" % (nbd_export,
                                                      target_path)
    logging.debug("Execute command %s: %s", cmd,
                  process.run(cmd, shell=True).stdout_text)


def get_img_data_size(img_path, driver='qcow2'):
    """
    Get the actual data size of a image

    :param img_path: The path to the image
    :return: The actual data size of the image in 'MB'.
    """
    cmd = "qemu-img map -f %s %s --output=json -U" % (driver, img_path)
    result = process.run(cmd, shell=True).stdout_text
    json_data = json.loads(result)
    summary = 0
    for entry in json_data:
        if entry["data"]:
            summary += entry["length"]
    return summary/1024/1024


def get_img_data_map(img_path, driver='qcow2'):
    """
    Get the data map of a image

    :prama img_path: The path to the image
    :return: The data map of the image
    """
    cmd = "qemu-img map -f %s %s --output=json -U" % (driver, img_path)
    result = process.run(cmd, shell=True).stdout_text
    json_data = json.loads(result)
    data_map = []
    for entry in json_data:
        if entry["data"]:
            data_map.append(entry)
    return data_map


def dump_data_to_file(data_map, source_file_path, target_file_path, driver='qcow2'):
    """
    Dump real data from the source file to a target file according to data map

    :param data_map: The data map used to indicate which part of the image
    containing real data
    :param source_file_path: The path to the source file
    :param target_file_path: The path to the target file
    :param driver: The image format of the source file
    """
    with open(target_file_path, 'w'):
        cmd = "qemu-io -f %s -c \"read -vC %s %s\" %s -r -U  | sed '$d'"
        for entry in data_map:
            cmd %= (driver, entry["start"], entry["length"], source_file_path)
            cmd += ">> %s" % target_file_path
            process.run(cmd, shell=True)


def cmp_backup_data(original_file, backup_file,
                    original_file_driver='qcow2', backup_file_driver='qcow2'):
    """
    Compare the backuped data and the original data

    :param original_file: The file containing the original data
    :param backup_file: The file containing the backuped data
    :param original_file_driver: Original image format
    :param backup_file_driver: Backup image format
    :return: Trun if data is correct, false if not
    """
    original_file_dump = original_file + ".dump"
    backup_file_dump = backup_file + ".dump"
    data_map = get_img_data_map(backup_file, backup_file_driver)
    dump_data_to_file(data_map, original_file, original_file_dump, original_file_driver)
    dump_data_to_file(data_map, backup_file, backup_file_dump, backup_file_driver)
    if filecmp.cmp(original_file_dump, backup_file_dump):
        return True
