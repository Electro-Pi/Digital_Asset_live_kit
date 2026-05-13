pipeline {
    agent any

    environment {
        HOST = "195.35.3.228"
        USER = "root"
        SCRIPT = "/home/Digital_Asset_live_kit/Digital_Asset_live_kit/deploy.sh"
    }

    stages {
        stage('Deploy') {
            steps {
                sh """
                ssh -o StrictHostKeyChecking=no ${USER}@${HOST} 'bash ${SCRIPT}'
                """
            }
        }
    }
}