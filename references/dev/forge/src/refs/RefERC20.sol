// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// OpenZeppelin ERC20 as is: no mint, burn, owner or pause. Name and symbol live in storage, not
/// in the runtime code, so they do not affect the bytes.
contract RefERC20 is ERC20 {
    constructor() ERC20("Reference", "REF") {}
}
