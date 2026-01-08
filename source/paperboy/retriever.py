import sqlite3
import os
from typing import Optional, Tuple

from .config import Settings


class RetrievalError(Exception):
    """Custom exception for paper retrieval errors"""
    pass


class PaperRetriever:
    def __init__(self, settings: Settings):
        self.index_db_path = settings.INDEX_DB_PATH
        self.tar_dir_path = settings.TAR_DIR_PATH
        
        # Validate configuration at startup
        self._validate_config()
        
        # Connect to database
        try:
            self.db_connection = sqlite3.connect(self.index_db_path)
        except sqlite3.Error as e:
            raise RetrievalError(f"Failed to connect to database: {e}")
    
    def _validate_config(self):
        """Validate the configuration settings"""
        if not self.index_db_path:
            raise RetrievalError("INDEX_DB_PATH not configured")
        
        if not self.tar_dir_path:
            raise RetrievalError("TAR_DIR_PATH not configured")
        
        if not os.path.exists(self.index_db_path):
            raise RetrievalError(f"Database file not found: {self.index_db_path}")
        
        if not os.path.exists(self.tar_dir_path):
            raise RetrievalError(f"Root directory not found: {self.tar_dir_path}")
        
        # Check if the directory structure looks like arXiv (has year subdirectories)
        year_dirs = [d for d in os.listdir(self.tar_dir_path) 
                    if os.path.isdir(os.path.join(self.tar_dir_path, d)) and d.isdigit()]
        
        if not year_dirs:
            raise RetrievalError(f"Root directory doesn't contain expected year subdirectories: {self.tar_dir_path}")
    
    def get_source_by_id(self, paper_id: str) -> Optional[bytes]:
        """
        Get paper source by ID.
        Returns None if paper not found, raises RetrievalError for other issues.
        """
        cursor = self.db_connection.cursor()
        cursor.execute(
            "SELECT archive_file, offset, size FROM paper_index WHERE paper_id = ?",
            (paper_id,)
        )
        result = cursor.fetchone()
        
        if result is None:
            return None
        
        archive_file, offset, size = result
        tar_file_path = os.path.join(self.tar_dir_path, archive_file)
        
        # Check if tar file exists
        if not os.path.exists(tar_file_path):
            raise RetrievalError(f"Archive file not found: {tar_file_path}")
        
        try:
            with open(tar_file_path, 'rb') as file:
                file.seek(offset)
                file_content = file.read(size)
                return file_content
        except PermissionError:
            raise RetrievalError(f"Permission denied accessing archive file: {tar_file_path}")
        except OSError as e:
            raise RetrievalError(f"Error reading archive file {tar_file_path}: {e}")
    
    def get_detailed_error(self, paper_id: str) -> Tuple[str, str]:
        """
        Get detailed error information for debugging.
        Returns (error_type, error_message) tuple.
        """
        try:
            # Check database connection
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM paper_index")
            total_papers = cursor.fetchone()[0]
            
            if total_papers == 0:
                return ("empty_database", "The database contains no papers. Please run the indexing script first.")
            
            # Check if paper exists
            cursor.execute(
                "SELECT archive_file, offset, size FROM paper_index WHERE paper_id = ?",
                (paper_id,)
            )
            result = cursor.fetchone()
            
            if result is None:
                # Check for similar paper IDs
                cursor.execute(
                    "SELECT paper_id FROM paper_index WHERE paper_id LIKE ? LIMIT 5",
                    (f"%{paper_id[:6]}%",)
                )
                similar = cursor.fetchall()
                similar_ids = [row[0] for row in similar]
                
                if similar_ids:
                    return ("paper_not_found", f"Paper ID '{paper_id}' not found. Similar papers: {', '.join(similar_ids[:3])}")
                else:
                    return ("paper_not_found", f"Paper ID '{paper_id}' not found in the database.")
            
            # Paper exists in DB, check file access
            archive_file, offset, size = result
            tar_file_path = os.path.join(self.tar_dir_path, archive_file)
            
            if not os.path.exists(tar_file_path):
                return ("archive_missing", f"Archive file not found: {tar_file_path}")
            
            if not os.access(tar_file_path, os.R_OK):
                return ("permission_denied", f"Permission denied accessing archive file: {tar_file_path}")
            
            return ("unknown_error", "Unknown error occurred during paper retrieval.")
            
        except sqlite3.Error as e:
            return ("database_error", f"Database error: {e}")
        except Exception as e:
            return ("system_error", f"System error: {e}")