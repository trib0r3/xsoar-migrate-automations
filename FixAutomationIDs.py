import argparse
from os import path
from os import scandir
from os import mkdir
import shutil
import logging
import yaml
import json
import subprocess
import regex

NAME_SUFFIX = "_migration"
CACHE_PATH = "./.fixids.cache.json"

STAGE1_ADD_SUFFIXES = "s1-add-suffixes"
STAGE2_UPDATE_IDS = "s2-fix-content"
STAGE3_APPLY_CHANGES = "s3-validate"
STAGES = [
    "all", STAGE1_ADD_SUFFIXES, STAGE2_UPDATE_IDS, STAGE3_APPLY_CHANGES
]

DIR_MIGRATION_PACK = "./Packs/Migration"
DIRS_CUSTOM_CONTENT = [
    "Classifiers", "Dashboards", "IncidentFields", "IncidentTypes", "IndicatorFields", "IndicatorTypes", 
    "Integrations", "Layouts", "Lists", "Playbooks", "PreProcessRules", "Reports", "TestPlaybooks", "Widgets"
]

ERR_OK                          = 0
ERR_INVALID_ARGUMENT            = 1
ERR_INVALID_PATH                = 2
ERR_INVALID_STAGE_NO_CACHE      = 3
ERR_NO_CHANGES                  = 4
ERR_NOT_FIXED                   = 5

class AutomationRecord:
    def __init__(self) -> None:
        self.original_name = ""
        self.name = ""
        self.id = ""
        self.path = ""

    def fromJson(self, fromdict: dict) -> None:
        self.original_name = fromdict['original_name']
        self.name = fromdict['name']
        self.id = fromdict['id']
        self.path = fromdict['path']
    
    def setValues(self, original_name, name, script_id, path) -> None:
        self.original_name = original_name
        self.name = name
        self.id = script_id
        self.path = path
    
class AutomationRecordEncoder(json.JSONEncoder):
    def default(self, o):
        return o.__dict__

def walk_yml(path, extensions=('.yml', '.yaml')):
    for e in scandir(path):
        if e.is_dir(follow_symlinks=False):
            yield from walk_yml(e.path)
        else:
            if e.name.lower().endswith(extensions):
                yield e.path

def cache_save(db_scripts: list):
    with open(CACHE_PATH, 'w') as f:
        json.dump(db_scripts, f, indent=4, cls=AutomationRecordEncoder)
        logging.info(f"Saved cache to {CACHE_PATH}")

def cache_load() -> list:
    ret = None
    with open(CACHE_PATH, 'r') as f:
        raw_entries = json.load(f)
        records = []
        for r in raw_entries:
            rec = AutomationRecord()
            rec.fromJson(r)
            records.append(rec)
        ret = records
        logging.info(f"Loaded cache from {CACHE_PATH}")
    return ret

def demisto_cmd(cmd: str):
    full_cmd = "demisto-sdk " + cmd
    proc = subprocess.Popen(
        full_cmd.split(' '),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    out = proc.stdout.read().decode('utf-8')
    ret = proc.returncode
    logging.debug(f"> {full_cmd} (exit: {ret})\n{out}")
    return ret, out

def get_automations_path() -> str:
    return path.join(DIR_MIGRATION_PACK, "Scripts")

def upload_to_xsoar(path):
    logging.info(f"Uploading changes to the demisto server ({path})")
    demisto_cmd(f'upload --insecure -i {path}')
    logging.info("Done")

def stage1_scripts_update(automations_path: str) -> list:
    """
    Get the list of scripts to update and update their name suffixes
    """
    # process yml 
    scripts = []
    for ymlpath in walk_yml(automations_path):
        f = open(ymlpath, 'r')
        yfile = yaml.safe_load(f)
        script_id = yfile['commonfields']['id']
        script_name = yfile['name']
        f.close()

        # process only scripts which has different ids
        if script_id != script_name:
            record = AutomationRecord()
            record.setValues(
                original_name=script_name,
                name=script_name + NAME_SUFFIX,
                script_id=script_id,
                path=ymlpath
            )
            scripts.append(record)

            # apply change to yml
            yfile['name'] = record.name
            f = open(ymlpath, 'w')
            yaml.dump(yfile, f)
            f.close()
    return scripts

def do_stage1():
    automations_path = get_automations_path()
    if not path.exists(automations_path):
        logging.error(f"Path {automations_path} doesn't exists!")
        return ERR_INVALID_PATH
    
    # delete the previous backup if exists
    pack_backup_path = DIR_MIGRATION_PACK + "-Backup"
    if path.exists(pack_backup_path):
        shutil.rmtree(pack_backup_path)
    
    # backup all content - just in case
    shutil.copytree(DIR_MIGRATION_PACK, pack_backup_path)
    
    # start processing
    db_scripts = stage1_scripts_update(automations_path)
    size = len(db_scripts)
    logging.info(f"Found {size} automations to be processed!")
    if size > 0:
        cache_save(db_scripts)
        upload_to_xsoar(automations_path)   
    return ERR_OK

def stage2_fix_automation(db: list) -> dict:
    "Fixes ids in scripts and returns the map old-id, new-id"
    ret = {}
    entry: AutomationRecord
    for entry in db:
        automation_yml = None
        
        with open(entry.path, 'r') as f:
            automation_yml = yaml.safe_load(f)
        automation_yml['commonfields']['id'] = entry.original_name
        automation_yml['name'] = entry.original_name
        ret[entry.id] = entry.original_name
        
        with open(entry.path, 'w') as f:    
            yaml.dump(automation_yml, f)
    return ret

def stage2_fix_dependency_ids(map_old_new: dict) -> dict:
    """
    Fixes ids in all locations, returns the list of changes
    """
    pattern = "|".join(map_old_new.keys())
    logging.debug(f"Regex pattern: {pattern}")
    rg_old_ids = regex.compile(pattern)
    changes = {}

    # visit old dirs, except scripts
    for directory in DIRS_CUSTOM_CONTENT:
        custom_data_dir = path.join(DIR_MIGRATION_PACK, directory)

        if not path.exists(custom_data_dir):
            logging.warning(f"Path doesn't exists (probably it doesn't have to). Skipping: {custom_data_dir}")
            continue
        logging.debug(f"Applying changes in: {custom_data_dir}")

        # visit all
        for yfile_path in walk_yml(custom_data_dir, extensions=('.yml', '.yaml', '.json')):
            data = ""
            with open(yfile_path, 'r') as f:
                data = f.read()
            
            # merge the same findings
            found_old_ids = list(set(rg_old_ids.findall(data)))
            for old_id in found_old_ids:
                # replace all occurences
                data = data.replace(old_id, map_old_new[old_id])
                chg = f"{old_id} -> {map_old_new[old_id]}"
                
                if yfile_path not in changes:
                    changes[yfile_path] = [chg]
                else:
                    changes[yfile_path].append(chg)
            
            if len(found_old_ids) > 0:
                with open(yfile_path, 'w') as f:
                    f.write(data)
    return changes

def do_stage2():
    """
    1. update id to the script name
    2. Update all yml files which are using script id to the new one
    3. Upload all content
    """
    db_scripts = cache_load()
    if db_scripts is None:
        logging.error("Missing cache file. Make sure to run stage 1!")
        return ERR_INVALID_STAGE_NO_CACHE

    logging.info("Fixing Automations..")
    map_old_new = stage2_fix_automation(db_scripts)
    upload_to_xsoar(get_automations_path())

    logging.info("Fixing dependencies...")
    changes = stage2_fix_dependency_ids(map_old_new)
    if len(changes) == 0:
        logging.error("Couldn't find the files requiring changing")
        return ERR_NO_CHANGES

    # save changelog
    logging.info(f"Changed {len(changes)} files")
    with open("changelog.json", "w") as f:
        json.dump(changes, f, indent=4)
    
    upload_to_xsoar(DIR_MIGRATION_PACK)
    return ERR_OK

def stage3_build_regex(db_list) -> str:
    oldids = []
    entry: AutomationRecord
    for entry in db_list:
        oldids.append(entry.id)
    return "|".join(oldids)

def do_stage3():
    """
    1. Download the custom content from local XSOAR instance
    2. Using demisto-sdk create dependency map and verify if automation with suffixes are used anywhere
    3. If automation with suffixes are not referenced anywhere, then it's OK to delete them
    """
    db_scripts = cache_load()
    if db_scripts is None:
        logging.error("Missing cache file. Make sure to run stage 1!")
        return ERR_INVALID_STAGE_NO_CACHE

    validation_dir_path = DIR_MIGRATION_PACK + "-Validate"
    if path.exists(validation_dir_path):
        shutil.rmtree(validation_dir_path)
    mkdir(validation_dir_path)

    logging.info(f"Downloading clean copy of custom content to local directory to {validation_dir_path}")
    demisto_cmd(f"download --insecure -a -o {validation_dir_path}")

    logging.info("Checking if anything contains reference to old automation id")
    old_ids_regex = regex.compile(stage3_build_regex(db_list=db_scripts))
    
    # traverse ALL files, not only whitelisted directories
    not_updated_files = []
    for file_path in walk_yml(validation_dir_path, extensions=('.yml', '.yaml', '.json')):
        suffix_patched = NAME_SUFFIX.replace("_", "") + "/"
        if suffix_patched in file_path:
            logging.debug(f"Skipping FP: {file_path}")
            continue
        
        data = None
        with open(file_path, 'r') as f:
            data = f.read()
        match = old_ids_regex.findall(data)
        if len(match) > 0:
            not_updated_files.append({"path": file_path, "match": match})
    
    if len(not_updated_files) == 0:
        logging.info("(´▽`ʃ♡ƪ) SUCCESS O(∩_∩)O")
        logging.info(f"Script managed to update all scripts and dependecies (custom content)!")
        return ERR_OK
    else:
        with open('not-fixed.json', 'w') as f:
            json.dump(not_updated_files, f, indent=4)
        logging.error(f"Some files are still containing dependencies o_O. Check ./not-fixed.json for more info")
        return ERR_NOT_FIXED

def main():
    logging.getLogger().setLevel(logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s","--stage", dest="stage", 
        required=True, choices=STAGES, 
        help="Select which stage you want to execute"
    )

    parser.add_argument(
        '-v', '--verbose', dest='verbose',
        action="store_true", default=False,
        help="Enable more verbose logging"
    )

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    execute_queue = []
    if args.stage == "all":
        execute_queue = STAGES[1:]
    else:
        execute_queue = [args.stage]
    
    try:
        for stage in execute_queue:
            if stage == STAGE1_ADD_SUFFIXES:
                ret = do_stage1()
                if ret != 0:
                    return ret
            elif stage == STAGE2_UPDATE_IDS:
                ret = do_stage2()
                if ret != 0:
                    return ret
            elif stage == STAGE3_APPLY_CHANGES:
                ret = do_stage3()
                if ret != 0:
                    return ret
            else:
                logging.error("Invalid stage value")
                return ERR_INVALID_ARGUMENT
    except KeyboardInterrupt:
        logging.error("Keyboard Interrupt detected. Modified files won't be restored automatically. Please do it manually")
    except Exception as ex:
        import traceback
        logging.error(f"Error {str(ex)}\n{traceback.format_exc()}")
        logging.error("Modified files won't be restored automatically. Please do it manually")

if __name__ == "__main__":
    main()