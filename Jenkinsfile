// Runs job_watch (banks and tech lists) Monday to Friday at 08:00, La Paz time.
// Setup steps: "Scheduled runs (Jenkins)" in README.md.
pipeline {
    agent any  // a Linux agent with python3 (>= 3.11) and internet access

    triggers {
        // Exactly 08:00 ("0", not Jenkins' spread-out "H"), in Bolivian time whatever
        // the server's own timezone is. 1-5 = Monday..Friday.
        cron('''TZ=America/La_Paz
0 8 * * 1-5''')
    }

    parameters {
        string(name: 'DATA_ROOT', defaultValue: '/srv/job-watch-data',
               description: 'Folder that keeps the snapshots between builds. ' +
                            'Empty = $HOME/job-watch-data of the user running the agent.')
    }

    options {
        disableConcurrentBuilds()                    // two runs would race on the snapshots
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '90'))
    }

    environment {
        PYTHONUNBUFFERED = '1'
        PYTHONIOENCODING = 'utf-8'                   // accented job titles in the console
    }

    stages {
        stage('Install') {
            steps {
                sh '''
                    rm -rf reports
                    python3 -m venv .venv
                    .venv/bin/pip install --quiet --upgrade pip
                    .venv/bin/pip install --quiet .
                '''
            }
        }

        stage('Run job_watch') {
            steps {
                script {
                    // Snapshots live outside the workspace, which Jenkins may wipe.
                    def root = params.DATA_ROOT?.trim() ?:
                        sh(returnStdout: true, script: 'printf %s "$HOME/job-watch-data"')
                    def errors = []
                    // [list, folder]: same folder names as the repo's data/ (jobs, tech)
                    for (entry in [['banks', 'jobs'], ['tech', 'tech']]) {
                        def watch = entry[0]
                        def status = sh(returnStatus: true,
                            script: "sh ci/run_job_watch.sh ${watch} '${root}/${entry[1]}' reports")
                        if (status == 2) {
                            unstable("${watch}: some sources failed (previous results kept); see reports/${watch}.txt")
                        } else if (status != 0) {
                            errors << "${watch} (exit ${status})"
                        }
                    }
                    if (errors) {
                        error("job_watch failed: ${errors.join(', ')}")
                    }
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'reports/*', allowEmptyArchive: true
        }
    }
}
