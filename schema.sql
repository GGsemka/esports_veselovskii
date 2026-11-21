-- Import into the database selected by the operator or MYSQL_DATABASE.
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nickname VARCHAR(20) NOT NULL UNIQUE,
    email VARCHAR(120) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('player','organizer','admin') NOT NULL DEFAULT 'player',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS games (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(120) NOT NULL UNIQUE,
    team_size_min INT NOT NULL,
    team_size_max INT NOT NULL,
    format ENUM('solo','team') NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (team_size_min >= 1 AND team_size_max >= team_size_min),
    CHECK (format <> 'solo' OR (team_size_min = 1 AND team_size_max = 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS teams (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(120) NOT NULL UNIQUE,
    captain_id INT NOT NULL UNIQUE,
    game_id INT NOT NULL,
    logo VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (captain_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS team_members (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_id INT NOT NULL,
    user_id INT NOT NULL UNIQUE,
    role ENUM('captain','player') NOT NULL DEFAULT 'player',
    joined_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE KEY uq_members_team_user (team_id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tournaments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(120) NOT NULL,
    description TEXT NOT NULL,
    game_id INT NOT NULL,
    organizer_id INT NOT NULL,
    format ENUM('single_elimination','round_robin') NOT NULL,
    registration_type ENUM('team','solo') NOT NULL,
    status ENUM('draft','published','in_progress','completed','cancelled') NOT NULL DEFAULT 'draft',
    max_participants INT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    banner VARCHAR(255) NULL,
    results_snapshot JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE RESTRICT,
    FOREIGN KEY (organizer_id) REFERENCES users(id) ON DELETE RESTRICT,
    CHECK (end_date > start_date),
    CHECK (max_participants BETWEEN 2 AND 64),
    INDEX idx_tournaments_status_created (status, created_at, id),
    INDEX idx_tournaments_game_status (game_id, status, created_at),
    INDEX idx_tournaments_created (created_at, id),
    INDEX idx_tournaments_history (status, updated_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS registrations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tournament_id INT NOT NULL,
    team_id INT NULL,
    user_id INT NULL,
    status ENUM('pending','approved','rejected','withdrawn') NOT NULL DEFAULT 'pending',
    rejection_reason VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_registrations_tournament_status (tournament_id,status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- MySQL does not permit a CHECK on these cascading foreign keys.
DELIMITER //
CREATE TRIGGER IF NOT EXISTS registrations_owner_insert BEFORE INSERT ON registrations
FOR EACH ROW
BEGIN
    IF (NEW.team_id IS NULL) = (NEW.user_id IS NULL) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Expected exactly one registration owner';
    END IF;
END//
CREATE TRIGGER IF NOT EXISTS registrations_owner_update BEFORE UPDATE ON registrations
FOR EACH ROW
BEGIN
    IF (NEW.team_id IS NULL) = (NEW.user_id IS NULL) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Expected exactly one registration owner';
    END IF;
END//
DELIMITER ;

CREATE TABLE IF NOT EXISTS matches (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tournament_id INT NOT NULL,
    round_number INT NOT NULL,
    match_number INT NOT NULL,
    registration1_id INT NULL,
    registration2_id INT NULL,
    winner_registration_id INT NULL,
    score1 INT NULL,
    score2 INT NULL,
    status ENUM('scheduled','played','walkover','cancelled') NOT NULL DEFAULT 'scheduled',
    next_match_id INT NULL,
    scheduled_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
    FOREIGN KEY (registration1_id) REFERENCES registrations(id) ON DELETE SET NULL,
    FOREIGN KEY (registration2_id) REFERENCES registrations(id) ON DELETE SET NULL,
    FOREIGN KEY (winner_registration_id) REFERENCES registrations(id) ON DELETE SET NULL,
    FOREIGN KEY (next_match_id) REFERENCES matches(id) ON DELETE SET NULL,
    CHECK (score1 IS NULL OR score1 >= 0),
    CHECK (score2 IS NULL OR score2 >= 0),
    UNIQUE KEY uq_matches_tournament_round_match (tournament_id, round_number, match_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chat_threads (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tournament_id INT NOT NULL,
    participant_user_id INT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
    FOREIGN KEY (participant_user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE KEY uq_threads_participant (tournament_id, participant_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chat_messages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    thread_id INT NOT NULL,
    sender_id INT NOT NULL,
    text TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_messages_thread_created (thread_id, created_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
