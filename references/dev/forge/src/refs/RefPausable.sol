// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

/// Module: OpenZeppelin's Pausable only exposes `paused()`. `pause()` and `unpause()` are the
/// names its documentation and the OpenZeppelin Wizard give the public wrappers, so this adds
/// them, with no access control because only the function list is used.
contract RefPausable is Pausable {
    function pause() external {
        _pause();
    }

    function unpause() external {
        _unpause();
    }
}
