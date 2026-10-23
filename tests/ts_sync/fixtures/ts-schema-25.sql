-- Target Scheduler schema at PRAGMA user_version = 25, tables only, no rows.
--
-- Reconstructed by replaying Target Scheduler's own migration scripts, not
-- dumped from a live database: Database/Initial/initial_schema.sql followed by
-- Database/Migrate/1.sql through 25.sql, taken from the release/nightly-3.3
-- branch of tcpalmer/nina.plugin.targetscheduler.
--
-- The same replay run out to 28 reproduces ts-schema-28.sql, which was dumped
-- from a real install, column for column including types and defaults, so the
-- reconstruction can be trusted at the intermediate versions too.
--
-- See docs/schema-history.md for what each version step changes.

PRAGMA user_version = 25;
CREATE TABLE `acquiredimage` (
	`Id`			INTEGER NOT NULL,
	`projectId`		INTEGER NOT NULL,
	`targetId`		INTEGER NOT NULL,
	`acquireddate`	INTEGER,
	`filtername`	TEXT NOT NULL,
	"gradingStatus"		INTEGER NOT NULL,
    `metadata`		TEXT NOT NULL, rejectreason TEXT, profileId TEXT, exposureId INTEGER DEFAULT 0, guid TEXT,
	PRIMARY KEY(`Id`)
);
CREATE TABLE `exposureplan` (
	`Id`			INTEGER NOT NULL,
	`profileId`		TEXT NOT NULL,
	`exposure`		REAL NOT NULL,
	`desired`		INTEGER,
	`acquired`		INTEGER,
	`accepted`		INTEGER,
	`targetid`		INTEGER,
	`exposureTemplateId`	INTEGER, enabled INTEGER DEFAULT 1, guid TEXT,
	FOREIGN KEY(`targetId`) REFERENCES `target`(`Id`),
	FOREIGN KEY(`exposureTemplateId`) REFERENCES `exposuretemplate`(`Id`),
	PRIMARY KEY(`Id`)
);
CREATE TABLE `exposuretemplate` (
	`Id`			INTEGER NOT NULL,
    `profileId`		TEXT NOT NULL,
    `name`			TEXT NOT NULL,
    `filtername`	TEXT NOT NULL,
	`gain`			INTEGER,
	`offset`		INTEGER,
	`bin`			INTEGER,
	`readoutmode`	INTEGER,
	`twilightlevel` INTEGER,
	`moonavoidanceenabled`	INTEGER,
	`moonavoidanceseparation`	REAL,
	`moonavoidancewidth`	INTEGER,
	`maximumhumidity`	REAL, defaultexposure REAL DEFAULT 60, moonrelaxscale REAL DEFAULT 0, moonrelaxmaxaltitude REAL DEFAULT 5, moonrelaxminaltitude REAL DEFAULT -15, moondownenabled INTEGER DEFAULT 0, ditherevery INTEGER DEFAULT -1, minutesOffset INTEGER DEFAULT 0, guid TEXT,
	PRIMARY KEY(`Id`)
);
CREATE TABLE "filtercadenceitem" (
   "Id"				INTEGER NOT NULL,
   "targetid"		INTEGER NOT NULL,
   "order"			INTEGER NOT NULL,
   "next"			INTEGER,
   "action"			INTEGER NOT NULL,
   "referenceIdx"	INTEGER,
   PRIMARY KEY("Id")
);
CREATE TABLE `flathistory` (
   `Id`        INTEGER NOT NULL,
   `targetId`         INTEGER,
   `lightSessionDate`   INTEGER,
   `flatsTakenDate`   INTEGER,
   `profileId`    TEXT NOT NULL,
   `flatsType`    TEXT,
   `filterName`    TEXT,
   `gain`         INTEGER,
   `offset`    INTEGER,
   `bin`       INTEGER,
   `readoutmode`  INTEGER,
   `rotation`        REAL,
   `roi`        REAL, lightSessionId INTEGER NOT NULL DEFAULT 0,
   PRIMARY KEY(`id`)
);
CREATE TABLE `imagedata` (
	`Id`			INTEGER NOT NULL,
	`tag`			TEXT,
	`imagedata`		BLOB,
	`acquiredimageid`	INTEGER, width INTEGER DEFAULT 0, height INTEGER DEFAULT 0,
	FOREIGN KEY(`acquiredImageId`) REFERENCES `acquiredimage`(`Id`),
	PRIMARY KEY(`Id`)
);
CREATE TABLE "overrideexposureorderitem" (
   "Id"				INTEGER NOT NULL,
   "targetid"		INTEGER NOT NULL,
   "order"			INTEGER NOT NULL,
   "action"			INTEGER NOT NULL,
   "referenceIdx"	INTEGER,
   PRIMARY KEY("Id")
);
CREATE TABLE `profilepreference` (
	`Id`			INTEGER NOT NULL,
	`profileId`		TEXT NOT NULL,
	`enableGradeRMS`	INTEGER,
	`enableGradeStars`	INTEGER,
	`enableGradeHFR`	INTEGER,
	`maxGradingSampleSize`		INTEGER,
	`rmsPixelThreshold`			REAL,
	`detectedStarsSigmaFactor`	REAL,
	`hfrSigmaFactor`			REAL, acceptimprovement INTEGER DEFAULT 1, exposurethrottle REAL DEFAULT 125, parkonwait INTEGER DEFAULT 0, enableSmartPlanWindow INTEGER DEFAULT 1, enableSynchronization INTEGER DEFAULT 0, syncWaitTimeout INTEGER DEFAULT 300, syncActionTimeout INTEGER DEFAULT 300, syncSolveRotateTimeout INTEGER DEFAULT 300, enableMoveRejected INTEGER DEFAULT 0, enableGradeFWHM INTEGER DEFAULT 0, enableGradeEccentricity INTEGER DEFAULT 0, fwhmSigmaFactor INTEGER DEFAULT 4, eccentricitySigmaFactor INTEGER DEFAULT 4, enableDeleteAcquiredImagesWithTarget INTEGER DEFAULT 1, syncEventContainerTimeout INTEGER DEFAULT 300, delayGrading REAL DEFAULT 80, autoAcceptLevelHFR REAL DEFAULT 0, autoAcceptLevelFWHM REAL DEFAULT 0, autoAcceptLevelEccentricity REAL DEFAULT 0, enableSimulatedRun INTEGER DEFAULT 0, skipSimulatedWaits INTEGER  DEFAULT 1, skipSimulatedUpdates INTEGER DEFAULT 0, enableSlewCenter INTEGER DEFAULT 1, logLevel INTEGER DEFAULT 3, enableStopOnHumidity INTEGER DEFAULT 1, guid TEXT, enableProfileTargetCompletionReset INTEGER DEFAULT 0, enableAPI INTEGER DEFAULT 0, apiPort INTEGER DEFAULT 8188, apiPrettyPrint INTEGER DEFAULT 0, enableSyncedAutoFocus INTEGER DEFAULT 0,
	PRIMARY KEY(`id`)
);
CREATE TABLE `project` (
	`Id`			INTEGER NOT NULL,
	`profileId`		TEXT NOT NULL,
	`name`			TEXT NOT NULL,
	`description`	TEXT,
	`state`			INTEGER,
	`priority`		INTEGER,
	`createdate`	INTEGER,
	`activedate`	INTEGER,
	`inactivedate`	INTEGER,
	`minimumtime`	INTEGER,
	`minimumaltitude`	REAL,
	`usecustomhorizon`	INTEGER,
	`horizonoffset`	REAL,
	`meridianwindow`	INTEGER,
	`filterswitchfrequency`	INTEGER,
	`ditherevery`	INTEGER,
	`enablegrader`	INTEGER, isMosaic INTEGER NOT NULL DEFAULT 0, flatsHandling INTEGER NOT NULL DEFAULT 0, maximumAltitude REAL DEFAULT 0, smartexposureorder INTEGER DEFAULT 0, guid TEXT,
	PRIMARY KEY(`id`)
);
CREATE TABLE `ruleweight` (
	`Id`			INTEGER NOT NULL,
	`name`			TEXT NOT NULL,
    `weight`		REAL NOT NULL,
	`projectid`		INTEGER,
	FOREIGN KEY(`projectId`) REFERENCES `project`(`Id`),
	PRIMARY KEY(`Id`)
);
CREATE TABLE `target` (
	`Id`			INTEGER NOT NULL,
	`name`			TEXT NOT NULL,
	`active`		INTEGER NOT NULL,
	`ra`			REAL,
	`dec`			REAL,
	`epochcode`		INTEGER NOT NULL,
	`rotation`		REAL,
	`roi`			REAL,
	`projectid`		INTEGER, unusedOEO TEXT, guid TEXT, priority INTEGER DEFAULT -1,
	FOREIGN KEY(`projectId`) REFERENCES `project`(`Id`),
	PRIMARY KEY(`id`)
);
