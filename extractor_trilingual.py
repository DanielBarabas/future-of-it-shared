#!/usr/bin/env python3
"""
GitHub Repository Commit Analyzer

This script clones a GitHub repository and analyzes all commits to extract:
1. Author information
2. Files touched in each commit
3. Libraries/scripts imported in TypeScript, JavaScript, and Swift files for each commit
"""

import os
import re
import json
import subprocess
import logging
from typing import List, Dict, Set, Optional
from pathlib import Path
import tempfile
import shutil
from datetime import datetime


class GitCommitAnalyzer:
    def __init__(self, repo_url: str, clone_dir: Optional[str] = None):
        self.repo_url = repo_url
        self.clone_dir = clone_dir or tempfile.mkdtemp()
        self.repo_path = None
        
        # Set up error logging
        self.setup_logging()
        
    def setup_logging(self):
        """Set up error logging to file and console."""
        # Create logs directory if it doesn't exist
        log_dir = 'logs'
        os.makedirs(log_dir, exist_ok=True)
        
        # Generate log filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        repo_name = self.repo_url.split('/')[-1].replace('.git', '')
        log_filename = f'{log_dir}/analyzer_{repo_name}_{timestamp}.log'
        
        # Configure logging
        self.logger = logging.getLogger('GitCommitAnalyzer')
        self.logger.setLevel(logging.DEBUG)
        
        # File handler for all logs
        file_handler = logging.FileHandler(log_filename)
        file_handler.setLevel(logging.DEBUG)
        
        # Console handler for warnings and errors
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # Add handlers to logger
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        self.logger.info(f"Logging initialized. Log file: {log_filename}")
        
    def clone_repository(self) -> str:
        """Clone the GitHub repository to a local directory."""
        repo_name = self.repo_url.split('/')[-1].replace('.git', '')
        self.repo_path = os.path.join(self.clone_dir, repo_name)
        
        print(f"Cloning repository to {self.repo_path}...")
        self.logger.info(f"Starting clone of {self.repo_url} to {self.repo_path}")
        
        try:
            subprocess.run(
                ['git', 'clone', self.repo_url, self.repo_path],
                check=True,
                capture_output=True,
                text=True
            )
            print(f"Repository cloned successfully to {self.repo_path}")
            self.logger.info(f"Repository cloned successfully")
            return self.repo_path
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to clone repository: {e.stderr}"
            self.logger.error(error_msg)
            raise Exception(error_msg)
    
    def get_all_commits(self) -> List[str]:
        """Get list of all commit hashes in chronological order."""
        try:
            result = subprocess.run(
                ['git', 'log', '--pretty=format:%H', '--reverse'],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            commits = result.stdout.strip().split('\n') if result.stdout.strip() else []
            self.logger.info(f"Retrieved {len(commits)} commits")
            return commits
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to get commits: {e.stderr}"
            self.logger.error(error_msg)
            raise Exception(error_msg)
    
    def get_commit_info(self, commit_hash: str) -> Dict:
        """Get basic commit information."""
        try:
            # Get author and commit message
            result = subprocess.run(
                ['git', 'show', '--pretty=format:%an|%ae|%s', '--name-only', commit_hash],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            
            lines = result.stdout.strip().split('\n')
            if not lines:
                self.logger.warning(f"No data returned for commit {commit_hash}")
                return {}
            
            # Parse author info and subject
            author_line = lines[0]
            author_name, author_email, subject = author_line.split('|', 2)
            
            # Get list of files (skip the first line which contains author info)
            files = [line for line in lines[1:] if line.strip()]
            
            return {
                'hash': commit_hash,
                'author': {
                    'name': author_name,
                    'email': author_email
                },
                'subject': subject,
                'files': files
            }
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to get info for commit {commit_hash}: {e.stderr}"
            self.logger.error(error_msg)
            print(f"Warning: {error_msg}")
            return {}
        except ValueError as e:
            error_msg = f"Failed to parse commit info for {commit_hash}: {e}"
            self.logger.error(error_msg)
            return {}
    
    def extract_typescript_imports(self, file_content: str) -> Set[str]:
        """Extract import statements from TypeScript file content."""
        imports = set()
        
        # Patterns to match different import styles
        import_patterns = [
            # import { something } from 'module'
            r"import\s+{[^}]*}\s+from\s+['\"]([^'\"]+)['\"]",
            # import something from 'module'
            r"import\s+\w+\s+from\s+['\"]([^'\"]+)['\"]",
            # import * as something from 'module'
            r"import\s+\*\s+as\s+\w+\s+from\s+['\"]([^'\"]+)['\"]",
            # import type { something } from 'module' (TypeScript specific)
            r"import\s+type\s+{[^}]*}\s+from\s+['\"]([^'\"]+)['\"]",
            # import 'module'
            r"import\s+['\"]([^'\"]+)['\"]",
            # const something = require('module')
            r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            # import() dynamic imports
            r"import\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"
        ]
        
        for pattern in import_patterns:
            try:
                matches = re.findall(pattern, file_content, re.MULTILINE)
                imports.update(matches)
            except re.error as e:
                self.logger.error(f"Regex error in TypeScript import extraction: {e}")
        
        return imports
    
    def extract_javascript_imports(self, file_content: str) -> Set[str]:
        """Extract import statements from JavaScript file content."""
        imports = set()
        
        # JavaScript import patterns (similar to TypeScript but without type imports)
        import_patterns = [
            # import { something } from 'module'
            r"import\s+{[^}]*}\s+from\s+['\"]([^'\"]+)['\"]",
            # import something from 'module'
            r"import\s+\w+\s+from\s+['\"]([^'\"]+)['\"]",
            # import * as something from 'module'
            r"import\s+\*\s+as\s+\w+\s+from\s+['\"]([^'\"]+)['\"]",
            # import 'module'
            r"import\s+['\"]([^'\"]+)['\"]",
            # const/let/var something = require('module')
            r"(?:const|let|var)\s+\w+\s*=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            # require('module')
            r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            # import() dynamic imports
            r"import\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"
        ]
        
        for pattern in import_patterns:
            try:
                matches = re.findall(pattern, file_content, re.MULTILINE)
                imports.update(matches)
            except re.error as e:
                self.logger.error(f"Regex error in JavaScript import extraction: {e}")
        
        return imports
    
    def extract_swift_imports(self, file_content: str) -> Set[str]:
        """Extract import statements from Swift file content."""
        imports = set()
        
        # Swift import patterns
        import_patterns = [
            # import Module
            r"^\s*import\s+(\w+)\s*$",
            # import Module.SubModule
            r"^\s*import\s+([\w\.]+)\s*$",
            # import struct Module.Type
            r"^\s*import\s+(?:struct|class|enum|protocol|typealias|func|let|var)\s+([\w\.]+)",
            # @testable import Module
            r"^\s*@testable\s+import\s+(\w+)\s*$",
            # import Module; import AnotherModule (multiple on same line)
            r"import\s+(\w+)(?:\s*;|\s*$)"
        ]
        
        for pattern in import_patterns:
            try:
                matches = re.findall(pattern, file_content, re.MULTILINE)
                imports.update(matches)
            except re.error as e:
                self.logger.error(f"Regex error in Swift import extraction: {e}")
        
        return imports
    
    def get_file_content_at_commit(self, commit_hash: str, file_path: str) -> Optional[str]:
        """Get the content of a file at a specific commit."""
        try:
            result = subprocess.run(
                ['git', 'show', f'{commit_hash}:{file_path}'],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            # File might not exist at this commit or other error
            self.logger.debug(f"Could not get content for {file_path} at commit {commit_hash}: {e}")
            return None
    
    def is_typescript_file(self, file_path: str) -> bool:
        """Check if a file is a TypeScript file."""
        return file_path.endswith(('.ts', '.tsx', '.mts', '.cts'))
    
    def is_javascript_file(self, file_path: str) -> bool:
        """Check if a file is a JavaScript file."""
        return file_path.endswith(('.js', '.jsx', '.mjs', '.cjs'))
    
    def is_swift_file(self, file_path: str) -> bool:
        """Check if a file is a Swift file."""
        return file_path.endswith('.swift')
    
    def analyze_commit(self, commit_hash: str) -> Dict:
        """Analyze a single commit for all required information."""
        commit_info = self.get_commit_info(commit_hash)
        if not commit_info:
            return {}
        
        typescript_imports = {}
        javascript_imports = {}
        swift_imports = {}
        
        # Analyze files for imports based on type
        for file_path in commit_info['files']:
            try:
                if self.is_typescript_file(file_path):
                    file_content = self.get_file_content_at_commit(commit_hash, file_path)
                    if file_content:
                        imports = self.extract_typescript_imports(file_content)
                        if imports:
                            typescript_imports[file_path] = list(imports)
                            
                elif self.is_javascript_file(file_path):
                    file_content = self.get_file_content_at_commit(commit_hash, file_path)
                    if file_content:
                        imports = self.extract_javascript_imports(file_content)
                        if imports:
                            javascript_imports[file_path] = list(imports)
                            
                elif self.is_swift_file(file_path):
                    file_content = self.get_file_content_at_commit(commit_hash, file_path)
                    if file_content:
                        imports = self.extract_swift_imports(file_content)
                        if imports:
                            swift_imports[file_path] = list(imports)
                            
            except Exception as e:
                self.logger.error(f"Error analyzing file {file_path} in commit {commit_hash}: {e}")
        
        commit_info['typescript_imports'] = typescript_imports
        commit_info['javascript_imports'] = javascript_imports
        commit_info['swift_imports'] = swift_imports
        
        return commit_info
    
    def analyze_all_commits(self) -> List[Dict]:
        """Analyze all commits in the repository."""
        if not self.repo_path:
            self.clone_repository()
        
        commits = self.get_all_commits()
        results = []
        
        print(f"Analyzing {len(commits)} commits...")
        self.logger.info(f"Starting analysis of {len(commits)} commits")
        
        for i, commit_hash in enumerate(commits, 1):
            print(f"Processing commit {i}/{len(commits)}: {commit_hash[:8]}")
            
            try:
                commit_data = self.analyze_commit(commit_hash)
                if commit_data:
                    results.append(commit_data)
            except Exception as e:
                error_msg = f"Error analyzing commit {commit_hash}: {e}"
                self.logger.error(error_msg)
                print(f"Warning: {error_msg}")
        
        self.logger.info(f"Analysis complete. Processed {len(results)} commits successfully")
        return results
    
    def save_results(self, results: List[Dict], output_file: str = 'commit_analysis.json'):
        """Save analysis results to a JSON file."""
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"Results saved to {output_file}")
            self.logger.info(f"Results saved to {output_file}")
        except Exception as e:
            error_msg = f"Failed to save results: {e}"
            self.logger.error(error_msg)
            print(f"Error: {error_msg}")
    
    def cleanup(self):
        """Clean up temporary directories."""
        if self.clone_dir and os.path.exists(self.clone_dir):
            try:
                shutil.rmtree(self.clone_dir)
                print(f"Cleaned up temporary directory: {self.clone_dir}")
                self.logger.info(f"Cleaned up temporary directory: {self.clone_dir}")
            except Exception as e:
                error_msg = f"Failed to cleanup directory: {e}"
                self.logger.error(error_msg)
                print(f"Warning: {error_msg}")


def main():
    """Main function to run the analysis."""
    # Example usage
    repo_url = input("Enter GitHub repository URL: ").strip()
    
    if not repo_url:
        print("Error: Repository URL is required")
        return
    
    analyzer = GitCommitAnalyzer(repo_url)
    
    try:
        # Analyze all commits
        results = analyzer.analyze_all_commits()
        
        # Print summary
        print(f"\n=== Analysis Summary ===")
        print(f"Total commits analyzed: {len(results)}")
        
        # Count files by type
        total_ts_files = sum(len(commit.get('typescript_imports', {})) for commit in results)
        total_js_files = sum(len(commit.get('javascript_imports', {})) for commit in results)
        total_swift_files = sum(len(commit.get('swift_imports', {})) for commit in results)
        
        print(f"Total TypeScript files with imports: {total_ts_files}")
        print(f"Total JavaScript files with imports: {total_js_files}")
        print(f"Total Swift files with imports: {total_swift_files}")
        
        # Save results
        output_file = f"analysis_{repo_url.split('/')[-1].replace('.git', '')}.json"
        analyzer.save_results(results, output_file)
        
        # Show a sample of the results
        if results:
            print(f"\n=== Sample Result (Latest Commit) ===")
            sample = results[-1]
            print(f"Commit: {sample['hash'][:8]}")
            print(f"Author: {sample['author']['name']} <{sample['author']['email']}>")
            print(f"Subject: {sample['subject']}")
            print(f"Files touched: {len(sample['files'])}")
            
            # Show TypeScript imports
            if sample.get('typescript_imports'):
                print("TypeScript imports found in:")
                for file_path, imports in sample['typescript_imports'].items():
                    print(f"  {file_path}: {', '.join(imports[:5])}")
                    if len(imports) > 5:
                        print(f"    ... and {len(imports) - 5} more")
            
            # Show JavaScript imports
            if sample.get('javascript_imports'):
                print("JavaScript imports found in:")
                for file_path, imports in sample['javascript_imports'].items():
                    print(f"  {file_path}: {', '.join(imports[:5])}")
                    if len(imports) > 5:
                        print(f"    ... and {len(imports) - 5} more")
            
            # Show Swift imports
            if sample.get('swift_imports'):
                print("Swift imports found in:")
                for file_path, imports in sample['swift_imports'].items():
                    print(f"  {file_path}: {', '.join(imports[:5])}")
                    if len(imports) > 5:
                        print(f"    ... and {len(imports) - 5} more")
    
    except KeyboardInterrupt:
        print("\nAnalysis interrupted by user")
        analyzer.logger.warning("Analysis interrupted by user")
    except Exception as e:
        print(f"Error during analysis: {e}")
        analyzer.logger.critical(f"Critical error during analysis: {e}")
    finally:
        # Optional: uncomment the next line to clean up the cloned repository
        # analyzer.cleanup()
        pass


if __name__ == "__main__":
    main()