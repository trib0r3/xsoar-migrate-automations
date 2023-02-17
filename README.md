# Fix Automation IDs
This script is fixing old ID naming convention to the new one based on the automation name.

## Problem statement
When automations are created from the UI the XSOAR creates their IDs by creating random UUID. When automation is created from demisto-sdk or installed as Content Pack XSOAR uses script name as ID.

This behavior creates conflicts when you try to move automations from UI development flow to Content Packs (CI&CD based), because you have scripts with identical names in XSOAR instance, but with different ID.

The script allows to safely move such scripts without risking of breaking existing playbooks in case the script will fail to move some content.

## Limitations
- if automation is configured in built-in content, then this script won't fix it, for example:
  - built-in incident types, fields and some pre-processing rules won't be affected
- custom content already installed as a content pack is no longer recognized as custom content by XSOAR, but system based (`demisto-sdk download -a` won't download it)

## Before use
1. `cd ./FixAutomationIDs`
2. Configure environment

`.env`

```txt
DEMISTO_BASE_URL=<XSOAR_URL>
DEMISTO_API_KEY=<XSOAR_API_KEY>
```

`.envrc`
```sh
export DEMISTO_BASE_URL="<XSOAR_URL>"
export DEMISTO_API_KEY="<XSOAR_API_KEY>"
```

3. Apply the config: `. ./.envrc`
4. Copy all content to be migrated to `./Packs/Migration`
```sh
# make sure to use demisto-sdk not newer than 1.9.0, newer are changing automations ID's to the automation name
demisto-sdk download -a -o ./Packs/Migration
```

## How it works (simplified)
1. Find which scripts should fixed
2. Create a fallback-copy of scripts with `_migration` suffix in name
3. Fix all IDs in automations, replace them also in all custom content
4. Verify if custom content is fixed correctly

## Migrate content
> **⚠ WARNING** Script will affect your environment. I strongly recommend to not execute it directly on prod server - follow the [Testing on separate instance](#testing-on-separate instance) to learn more.

### Step 1. Configure suffixes
After this step:
- all scripts will be renamed (with suffix *_migration* by default) and uploaded to XSOAR instance
- local file `.fixids.cache.json` is created - this file represents the state of the files which will be modified
   - *original_name* - original automation/script name
   - *name* - copy script name which will be created in the next stage
   - *id* - original id of the automation/script
   - *path* - relative path to the yml containing automation configuration

Side-effects (on XSOAR):
- scripts which executes differents scripts by their name will fail to execute them

```bash
python3 FixAutomationIDs.py -s s1-add-suffixes
```

### Step 2. Fix Content
This step consists of 2 smaller steps which are executed just after each other
```bash
python3 FixAutomationIDs.py -s s2-fix-content
```

#### Step 2.A Fix Automations
> Performed between logs: *Fixing Automations..* and *Fixing dependencies...*

After this step
- automations will have changed names (to the original values) and ids to the script names
- automations will be uploaded to XSOAR

Side-effects (on XSOAR):
- The copy of the affected automations will be created, because uploaded automations are now build from the different name and id, than their existing copies already existing on the XSOAR
- automations: 
   - without suffix: automations with fixed ID (and proper name)
   - with suffix: automations with their original ID
- side-effect from *Step 1* is now fixed
   - automations executing other automations are now going to call automations with fixed id
   - playbooks and other custom content are still going to use the automation with suffix in name 

#### Step 2.B Fix Content
> Performed after log: *Fixing dependencies...*

After this step:
- All custom data (playbooks, layouts, etc) will be pointing to the copy of automation with original name and new ID
- All custom data is uploaded to XSOAR

Side-effects (on XSOAR):
- All side-effects from previous steps are no longer valid
- on XSOAR you will see automations:
   - with their original names - they are now used by other automations and other custom data (playbooks, layouts, etc)
   - with suffix - with original automation id, not used by anything

### Step 3. Validation
After this step:
- The fresh copy of the all custom-data will be re-downloaded to the `./Packs/Migration-Validate` directory
- All content will be scanned against original ids of the automations
   - Skipped directories are shown by increasing verbosity (`-v`) - only files with `<suffix>/` in directory name should be ignored
- You will receive the log if migration were successful

### Step 4. Review content manually
Go over XSOAR, review the places where you are using your automations:
- incident types
- incident fields
- pre-processing rules
- etc

If you are seeing that something points to automation with `_migration` suffix then fix it manually.

Then you should be safe to delete all scripts with `_migration` suffix.

```bash
python3 FixAutomationIDs.py -s s3-validate
```
## Testing on separate instance
If you want to test the script on separate test instance of xsoar:
1. Setup XSOAR on VM
2. Follow the steps *Before Use* and provide the credentials for test instance
4. Upload it to the test env: `demisto-sdk upload --insecure -i ./Packs/Migration`
5. Follow *Migrate Content* steps
6. If everything works as expected:
   1. Repeat all steps on PROD
